#!/bin/bash
# Spot fleet for the block pull. Each worker pulls one slot range from publicnode (its rate limit is
# per IP, so N workers pull N times faster), decodes it to swap Parquet on the box, pushes raw and
# decoded chunks to S3 and terminates itself. No SSH: the worker is entirely user-data.
#   pull_fleet.sh launch RUN START END N [W0] slots [START, END) over N workers numbered from W0
#   pull_fleet.sh status RUN                 instances, done markers, last log line per worker
#   pull_fleet.sh fetch RUN                  decoded swaps to data/interim/swaps_RUN (raw stays in S3)
#   pull_fleet.sh terminate RUN              stop every instance tagged run=RUN
# Only touches instances tagged project=trenches-live and run=RUN. Every worker shuts down by
# DEADLINE_MIN regardless (instance-initiated shutdown = terminate).
set -uo pipefail
R=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export AWS_PROFILE=trenches-v2 AWS_DEFAULT_REGION=us-east-1
ACCOUNT=AWS_ACCOUNT_ID BUCKET=trenches-train-AWS_ACCOUNT_ID DEADLINE_MIN=${DEADLINE_MIN:-180}
TYPES=${TYPES:-"c7a.xlarge c6a.xlarge m7a.xlarge c7i.xlarge"}
RPS=${RPS:-7}
MARKET=${MARKET:-spot}       # MARKET=ondemand for a relaunch that must not be interrupted
log() { echo "[$(date -u +%H:%M:%S)] $*"; }

session() {
  [ "$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" = "$ACCOUNT" ] \
    || { echo "NEEDS_AWS_LOGIN: aws login --remote --profile trenches-v2-login"; exit 3; }
}

userdata() {  # worker W pulls [S, E)
  local run=$1 w=$2 s=$3 e=$4
  cat <<EOF
#!/bin/bash
shutdown -h +$DEADLINE_MIN
exec > /var/log/tl-worker.log 2>&1
set -x
B=s3://$BUCKET/trenches-live/$run
export DEBIAN_FRONTEND=noninteractive HOME=/root
apt-get update -qq && apt-get install -y -qq python3-venv python3-pip
python3 -m venv /v && /v/bin/pip install -q awscli aiohttp orjson base58 pandas pyarrow duckdb numpy
mkdir -p /w && cd /w && /v/bin/aws s3 cp \$B/code.tgz . && tar xzf code.tgz
( while sleep 60; do /v/bin/aws s3 cp /var/log/tl-worker.log \$B/logs/worker_$w.log --quiet
  /v/bin/aws s3 sync /w/raw \$B/raw/ --exclude '*.tmp' --quiet; done ) &   # chunks survive a spot loss
TL_RPC_SPECS="https://solana-rpc.publicnode.com=$RPS" /v/bin/python -m research.sandwich.pull --start $s --end $e --out /w/raw
/v/bin/python -m research.sandwich.scan --raw /w/raw --out /w/swaps --workers 3
/v/bin/aws s3 sync /w/raw \$B/raw/ --quiet
/v/bin/aws s3 sync /w/swaps \$B/swaps/ --quiet
ls /w/raw | wc -l > /w/n_chunks
/v/bin/aws s3 cp /w/n_chunks \$B/done/worker_$w
/v/bin/aws s3 cp /var/log/tl-worker.log \$B/logs/worker_$w.log --quiet
shutdown -h now
EOF
}

launch() {
  local run=$1 start=$2 end=$3 n=$4 w0=${5:-0}
  session
  local per=$(( ((end - start) / n + 199) / 200 * 200 ))
  tar czf /tmp/tl_code_$run.tgz -C "$R" --exclude=__pycache__ tlive research
  aws s3 cp /tmp/tl_code_$run.tgz s3://$BUCKET/trenches-live/$run/code.tgz --quiet
  local ami sg subnets
  ami=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
  sg=$(aws ec2 describe-security-groups --group-names trenches-train-ssh --query 'SecurityGroups[0].GroupId' --output text)
  subnets=($(aws ec2 describe-subnets --filters Name=default-for-az,Values=true --query 'Subnets[].SubnetId' --output text))
  local tags="[{Key=project,Value=trenches-live},{Key=run,Value=$run},{Key=Name,Value=tl-$run}]"
  mkdir -p "$R/logs/$run"
  for i in $(seq 0 $((n - 1))); do
    local w=$((w0 + i)) s=$((start + i * per)) e=$((start + (i + 1) * per)); [ $e -gt "$end" ] && e=$end
    [ $s -ge "$end" ] && break
    userdata "$run" "$w" "$s" "$e" > "$R/logs/$run/userdata_$w.sh"
    local ok=
    for t in $TYPES; do
      for k in "${!subnets[@]}"; do
        local sub=${subnets[$(( (w + k) % ${#subnets[@]} ))]}
        local mk=(--instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}')
        [ "$MARKET" = ondemand ] && mk=()
        id=$(aws ec2 run-instances --image-id "$ami" --instance-type "$t" --key-name trenches-train \
          --security-group-ids "$sg" --subnet-id "$sub" --count 1 --iam-instance-profile Name=trenches-train-ec2 \
          "${mk[@]}" \
          --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=40,VolumeType=gp3,DeleteOnTermination=true}' \
          --tag-specifications "ResourceType=instance,Tags=$tags" "ResourceType=volume,Tags=$tags" \
          --instance-initiated-shutdown-behavior terminate --metadata-options HttpTokens=required \
          --user-data "file://$R/logs/$run/userdata_$w.sh" --query 'Instances[0].InstanceId' --output text 2>/dev/null) && { ok=1; break 2; }
      done
    done
    [ -n "$ok" ] && log "worker $w [$s, $e) -> $id ($t)" || log "worker $w [$s, $e): NO_CAPACITY"
    echo "$w $s $e ${id:-none}" >> "$R/logs/$run/workers.txt"
  done
}

status() {
  local run=$1
  session
  aws ec2 describe-instances --filters Name=tag:run,Values="$run" Name=tag:project,Values=trenches-live \
    --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name]' --output text
  echo "done: $(aws s3 ls s3://$BUCKET/trenches-live/$run/done/ | wc -l)"
  for f in $(aws s3 ls s3://$BUCKET/trenches-live/$run/logs/ | awk '{print $4}'); do
    echo "$f: $(aws s3 cp s3://$BUCKET/trenches-live/$run/logs/$f - 2>/dev/null | grep -E '^[0-9]+ blocks=|\.jsonl\.gz [0-9]+$|Traceback|Error' | tail -n 1)"
  done
}

fetch() {
  local run=$1
  session
  aws s3 sync s3://$BUCKET/trenches-live/$run/swaps/ "$R/data/interim/swaps_$run/" --quiet
  ls "$R/data/interim/swaps_$run" | wc -l
}

terminate() {
  local run=$1
  session
  ids=$(aws ec2 describe-instances --filters Name=tag:run,Values="$run" Name=tag:project,Values=trenches-live \
    Name=instance-state-name,Values=pending,running --query 'Reservations[].Instances[].InstanceId' --output text)
  [ -n "$ids" ] && aws ec2 terminate-instances --instance-ids $ids --query 'TerminatingInstances[].InstanceId' --output text
}

cmd=$1; shift; "$cmd" "$@"
