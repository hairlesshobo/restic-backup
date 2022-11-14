
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

YML="${SCRIPT_DIR}/config.yml"

export AWS_ACCESS_KEY_ID=$(yq -r .env.AWS_ACCESS_KEY_ID $YML)
export AWS_SECRET_ACCESS_KEY=$(yq -r .env.AWS_SECRET_ACCESS_KEY $YML)
export RESTIC_PASSWORD=$(yq -r .env.RESTIC_PASSWORD $YML)
export RESTIC_REPOSITORY=$(yq -r .env.RESTIC_REPOSITORY $YML)
export RESTIC_KEY_HINT=$(yq -r .env.RESTIC_KEY_HINT $YML)
export RESTIC_PACK_SIZE=$(yq -r .env.RESTIC_PACK_SIZE $YML)
export RESTIC_PROGRESS_FPS=$(yq -r .env.RESTIC_PROGRESS_FPS $YML)

