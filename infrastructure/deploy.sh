#!/bin/bash -e

# This script should always run as if it were being called from
# the directory it lives in.
script_directory="$(perl -e 'use File::Basename;
 use Cwd "abs_path";
 print dirname(abs_path(@ARGV[0]));' -- "$0")"
cd "$script_directory" || exit

print_description() {
    # shellcheck disable=SC2016
    echo 'This script can be used to deploy and update a `refine.bio` instance stack.'
    echo 'It will create all of the AWS infrasctructure (roles/instances/db/network/etc),'
    echo 'open an ingress, kill all running Batch jobs, perform a database migration,'
    echo 're-define and re-register Batch job specifications, and finally close the'
    echo 'ingress. This can be run from a CI/CD machine or a local dev box.'
}

print_options() {
    echo 'This script accepts the following arguments: -e, -d, -i, -v, -u, -r, and -h.'
    echo '-h prints this help message and exits.'
    echo '-e specifies the environment you would like to deploy to and is not optional. Its valid values are:'
    echo '   "-e prod" will deploy the production stack. This should only be used from a CD machine.'
    echo '   "-e staging" will deploy the staging stack. This should only be used from a CD machine.'
    echo '   "-e dev" will deploy a dev stack which is appropriate for a single developer to use to test.'
    echo '-d May be used to override the Dockerhub repo where the images will be pulled from.'
    echo '   This may also be specified by setting the TF_VAR_dockerhub_repo environment variable.'
    echo '   If unset, defaults to "ccdlstaging" if the version contains "-dev" and "ccdl" otherwise.'
    echo '   for dev and staging environments and "ccdl" for prod.'
    echo '   This option is useful for testing code changes. Images with the code to be tested can be pushed'
    echo '   to your private Dockerhub repo and then the system will find them.'
    echo '-i specifies to use On Demand EC2 instances instead of Spot instances.'
    echo '-v specifies the version of the system which is being deployed and is not optional.'
    echo "-u specifies the username of the deployer. Should be the developer's name in development stacks."
    echo '   This option may be omitted, in which case the TF_VAR_user variable MUST be set instead.'
    echo '-r specifies the AWS region to deploy the stack to. Defaults to us-east-1.'
}

while getopts ":e:d:i:v:u:r:h" opt; do
    case $opt in
    e)
        export env=$OPTARG
        export TF_VAR_stage=$OPTARG
        ;;
    d)
        export TF_VAR_dockerhub_repo=$OPTARG
        ;;
    i)
        export TF_VAR_batch_use_on_demand_instances=$OPTARG
        ;;
    v)
        export SYSTEM_VERSION=$OPTARG
        export TF_VAR_system_version=$OPTARG
        ;;
    u)
        export TF_VAR_user=$OPTARG
        ;;
    r)
        export TF_VAR_region=$OPTARG
        ;;
    h)
        print_description
        echo
        print_options
        exit 0
        ;;
    \?)
        echo "Invalid option: -$OPTARG" >&2
        print_options >&2
        exit 1
        ;;
    :)
        echo "Option -$OPTARG requires an argument." >&2
        print_options >&2
        exit 1
        ;;
    esac
done

if [[ $env != "dev" && $env != "staging" && $env != "prod" ]]; then
    echo 'Error: must specify environment as either "dev", "staging", or "prod" with -e.'
    exit 1
fi

if [[ -z $TF_VAR_user ]]; then
    echo 'Error: must specify the username by either providing the -u argument or setting TF_VAR_user.'
    exit 1
fi

if [[ -z $SYSTEM_VERSION ]]; then
    echo 'Error: must specify the system version with -v.'
    exit 1
fi

if [[ -z $TF_VAR_dockerhub_repo ]]; then
    if [[ $SYSTEM_VERSION == *"-dev" ]]; then
        export TF_VAR_dockerhub_repo=ccdlstaging
    else
        export TF_VAR_dockerhub_repo=ccdl
    fi
fi

if [[ -z $TF_VAR_region ]]; then
    TF_VAR_region=us-east-1
fi


# We have terraform output environment variables via a single output
# variable, which we then read in as json using the command line tool
# `jq`, so that we can use them via bash.
format_environment_variables () {
  json_env_vars=$(terraform output -json environment_variables | jq -c '.[]')
  for row in $json_env_vars; do
      name=$(echo "$row" | jq -r ".name")
      value=$(echo "$row" | jq -r ".value")
      env_var_assignment="$name=$value"
      # Exporting an expansion rather than a variable, which is exactly what we want to do.
      # shellcheck disable=SC2163
      export "${env_var_assignment?}"
      echo "$env_var_assignment" >> prod_env
  done
}


# Load $ALL_CCDL_IMAGES and helper functions
source ../scripts/common.sh
# Make our IP address known to terraform.
TF_VAR_host_ip="$(dig +short myip.opendns.com @resolver1.opendns.com)"
export TF_VAR_host_ip

for IMAGE in $ALL_CCDL_IMAGES; do
    # For each image we need to set the env var that is used by our
    # scripts and the env var that gets picked up by terraform because
    # it is preceeded with TF_VAR.
    IMAGE_UPPER="$(echo "$IMAGE" | tr '[:lower:]' '[:upper:]')"
    export "${IMAGE_UPPER}_DOCKER_IMAGE=dr_$IMAGE:$SYSTEM_VERSION"
    export "TF_VAR_${IMAGE}_docker_image=dr_$IMAGE:$SYSTEM_VERSION"
done

# Copy ingress config to top level so it can be applied.
cp deploy/ci_ingress.tf .

# Check if a new ccdl-ubuntu ami will be needed for this region
if [[ $(aws ec2 describe-images \
            --region $TF_VAR_region --owners 589864003899 \
            --filters 'Name=name,Values=ccdl-ubuntu-18.04-*' \
            --query 'length(Images)') \
            -eq 0 ]]; then
    echo "No ccdl-ubuntu-18.04 AMI found for this region, creating a new one"

    # Find most recent ccdl-ubuntu ami from us-east-1
    template_ami_id=$(aws ec2 describe-images \
                          --region us-east-1 --owners 589864003899 \
                          --filters 'Name=name,Values=ccdl-ubuntu-18.04-*' \
                          --query 'sort_by(Images,&CreationDate)[-1].ImageId' \
                          --output text)

    # Make a copy into this region
    new_ami_name="ccdl-ubuntu-18.04-$(date "+%Y-%m-%dT%H.%M.%S")"
    new_ami_id=$(aws ec2 copy-image \
                     --source-image-id "$template_ami_id" \
                     --source-region us-east-1 \
                     --region "$TF_VAR_region" \
                     --name "$new_ami_name" \
                     --output text)
    echo "Created new AMI for $TF_VAR_region"
    echo "    name: $new_ami_name"
    echo "    id:   $new_ami_id"
fi

# Always init terraform first, especially since we're using a remote backend.
./init_terraform.sh

# Terraform doesn't manage these well, so they need to be tainted if
# they exist to ensure they won't require manual intervention.
terraform taint module.batch.aws_launch_template.data_refinery_launch_template || true
if terraform state list | grep -q module.batch.aws_batch_job_queue.data_refinery_; then
    terraform state list \
        | grep module.batch.aws_batch_job_queue.data_refinery_ \
        | xargs -L 1 terraform taint \
        || true
fi
if terraform state list | grep -q module.batch.aws_batch_compute_environment.data_refinery__; then
    terraform state list \
        | grep module.batch.aws_batch_compute_environment.data_refinery_ \
        | xargs -L 1 terraform taint \
        || true
fi

if terraform output | grep -q 'No outputs found'; then
    ran_init_build=true
    echo "No existing stack detected, applying initial terraform deployment."

    # These files are inputs but are created by format_batch_with_env.sh
    # based on outputs from terraform. Kinda a Catch 22, but we can
    # get around it by providing dummy files to get bootstrapped.
    touch api-configuration/environment
    touch foreman-configuration/environment

    # Output the plan for debugging deployments later.
    # Until terraform plan supports -var-file the plan is wrong.
    # terraform plan

    if [[ -n "$GITHUB_ACTIONS" ]]; then
        # Make sure we can't expose secrets in circleci
        terraform apply -var-file="environments/$env.tfvars" -auto-approve > /dev/null
    else
        terraform apply -var-file="environments/$env.tfvars" -auto-approve
    fi
fi

# We have to do this once before the initial deploy..
rm -f prod_env
format_environment_variables

../scripts/format_batch_with_env.sh -p api -e "$env" -o "$(pwd)/api-configuration/"
../scripts/format_batch_with_env.sh -p foreman -e "$env" -o "$(pwd)/foreman-configuration/"

if [[ -z $ran_init_build ]]; then
    # Open up ingress to AWS for Circle, stop jobs, migrate DB.
    echo "Deploying with ingress.."

    # Output the plan for debugging deployments later.
    # Until terraform plan supports -var-file the plan is wrong.
    # terraform plan

    if [[ -n "$GITHUB_ACTIONS" ]]; then
        # Make sure we can't expose secrets in circleci
        terraform apply -var-file="environments/$env.tfvars" -auto-approve > /dev/null
    else
        terraform apply -var-file="environments/$env.tfvars" -auto-approve
    fi
fi

# Make sure that prod_env is empty since we are only appending to it.
# prod_env is a temporary file we use to pass environment variables to
# `docker run` commands when running migrations.
rm -f prod_env

# (cont'd) ..and once again after the update when this is re-run.
format_environment_variables

# Make sure to clear out any old batch job templates since we
# will register everything in this directory.
if [ -e batch-job-templates ]; then
  rm -r batch-job-templates
fi

# Template the environment variables for production into the Batch Job
# definitions and API confs.
mkdir -p batch-job-templates
../scripts/format_batch_with_env.sh -p workers -e "$env" -o "$(pwd)/batch-job-templates"
../scripts/format_batch_with_env.sh -p surveyor -e "$env" -o "$(pwd)/batch-job-templates"

# API and foreman aren't run as Batch jobs, but the templater still works.
../scripts/format_batch_with_env.sh -p foreman -e "$env" -o "$(pwd)/foreman-configuration"
../scripts/format_batch_with_env.sh -p api -e "$env" -o "$(pwd)/api-configuration/"

# Remove all Batch jobs because it's the only way to be sure we don't
# have any old ones. Deleting the job queue is the easiest way to do
# this, and it will be recreated by the following run of terraform
# anyway.
python3 delete_batch_job_queue.py

# If we don't deregister these, they'll stick around and accumulate.
python3 deregister_batch_job_definitions.py

# Re-register Batch jobs (skip those that end in .tpl)
echo "Registering new job specifications.."
export AWS_DEFAULT_REGION=$AWS_REGION
# SC2010: Don't use ls | grep. Use a glob or a for loop with a condition to allow non-alphanumeric filenames.
# We are using a glob, but we want to limit it to a specific directory. Seems like an over aggressive check.
# shellcheck disable=SC2010
for batch_job_template in $(ls -1 batch-job-templates/*.json | grep -v .tpl); do
    aws batch register-job-definition --cli-input-json file://"$batch_job_template" &
    sleep 1
done
echo "Job registrations have been fired off."

# Get an image to run the migrations with.
docker pull "$DOCKERHUB_REPO/$FOREMAN_DOCKER_IMAGE"

# Test that the pg_bouncer instance is up. 15 minutes should be more than enough.
start_time=$(date +%s)
diff=0
until pg_isready -d "$DATABASE_NAME" -h "$DATABASE_PUBLIC_HOST" -p "$DATABASE_PORT" -U "$DATABASE_USER" &> /dev/null || [ "$diff" -gt "900" ]
do
    echo "Waiting for the pg_bouncer instance to come online ..."
    sleep 10
    (( diff = $(date +%s) - start_time ))
done

if ! pg_isready -d "$DATABASE_NAME" -h "$DATABASE_PUBLIC_HOST" -p "$DATABASE_PORT" -U "$DATABASE_USER" &> /dev/null; then
    echo "pg_bouncer instance failed to come up after 15 minutes."
    exit 1
fi

# Migrate auth.
docker run \
       --env-file prod_env \
       --env RUNNING_IN_CLOUD=False \
       --env DATABASE_HOST="$DATABASE_PUBLIC_HOST" \
       "$DOCKERHUB_REPO/$FOREMAN_DOCKER_IMAGE" python3 manage.py migrate auth

# Apply general migrations.
docker run \
       --env-file prod_env \
       --env RUNNING_IN_CLOUD=False \
       --env DATABASE_HOST="$DATABASE_PUBLIC_HOST" \
       "$DOCKERHUB_REPO/$FOREMAN_DOCKER_IMAGE" python3 manage.py migrate

# Create the cache table if it does not already exist.
docker run \
       --env-file prod_env \
       --env RUNNING_IN_CLOUD=False \
       --env DATABASE_HOST="$DATABASE_PUBLIC_HOST" \
       "$DOCKERHUB_REPO/$FOREMAN_DOCKER_IMAGE" python3 manage.py createcachetable

# Terraform doesn't manage these well, so they need to be tainted to
# ensure they won't require manual intervention.
terraform taint module.batch.aws_launch_template.data_refinery_launch_template
terraform state list \
    | grep module.batch.aws_batch_job_queue.data_refinery_ \
    | xargs -L 1 terraform taint \
    || true

# Ensure the latest image version is being used for the Foreman
terraform taint aws_instance.foreman_server_1

# Remove the ingress config so the next `terraform apply` will remove
# access for Circle.
# echo "Removing ingress.."
# rm ci_ingress.tf

if [[ -n "$GITHUB_ACTIONS" ]]; then
    # Make sure we can't expose secrets in circleci
    terraform apply -var-file="environments/$env.tfvars" -auto-approve > /dev/null
else
    terraform apply -var-file="environments/$env.tfvars" -auto-approve
fi

# We try to avoid rebuilding the API server because we can only run certbot
# 5 times a week. Therefore we pull the newest image and restart the API
# this way rather than by tainting the server like we do for foreman.
chmod 600 data-refinery-key.pem
API_IP_ADDRESS=$(terraform output -json api_server_1_ip | tr -d '"')

# To check to see if the docker container needs to be stopped before
# it can be started, grep for the name of the container. However if
# it's not found then grep will return a non-zero exit code so in that
# case return an empty string.
container_running=$(ssh -o StrictHostKeyChecking=no \
                        -o ServerAliveInterval=15 \
                        -o ConnectTimeout=5 \
                        -i data-refinery-key.pem \
                        "ubuntu@$API_IP_ADDRESS"  "docker ps -a" 2> /dev/null | grep dr_api || echo "")

# If $container_running is empty, then it's because the container isn't running.
# If the container isn't running, then it's because the instance is spinning up.
# The container will be started by the API's init script, so no need to do anything more.

# However if $container_running isn't empty then we need to stop and restart it.
if [[ -n $container_running ]]; then
    echo "Restarting API with latest image."

    # shellcheck disable=SC2029
    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=15 \
        -o ConnectTimeout=5 \
        -i data-refinery-key.pem \
        "ubuntu@$API_IP_ADDRESS" "docker pull $DOCKERHUB_REPO/$API_DOCKER_IMAGE"

    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=15 \
        -o ConnectTimeout=5 \
        -i data-refinery-key.pem \
        "ubuntu@$API_IP_ADDRESS" "docker rm -f dr_api"

    scp -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=15 \
        -o ConnectTimeout=5 \
        -i data-refinery-key.pem \
        api-configuration/environment "ubuntu@$API_IP_ADDRESS:/home/ubuntu/environment"

    # shellcheck disable=SC2029
    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=15 \
        -o ConnectTimeout=5 \
        -i data-refinery-key.pem \
        "ubuntu@$API_IP_ADDRESS" "docker run \
       --env-file environment \
       -e DATABASE_HOST=$DATABASE_HOST \
       -e DATABASE_NAME=$DATABASE_NAME \
       -e DATABASE_USER=$DATABASE_USER \
       -e DATABASE_PASSWORD=$DATABASE_PASSWORD \
       -e ELASTICSEARCH_HOST=$ELASTICSEARCH_HOST \
       -e ELASTICSEARCH_PORT=$ELASTICSEARCH_PORT \
       -v /tmp/volumes_static:/tmp/www/static \
       --log-driver=awslogs \
       --log-opt awslogs-region=$AWS_REGION \
       --log-opt awslogs-group=data-refinery-log-group-$USER-$STAGE \
       --log-opt awslogs-stream=log-stream-api-$USER-$STAGE \
       -p 8081:8081 \
       --name=dr_api \
       -it -d $DOCKERHUB_REPO/$API_DOCKER_IMAGE /bin/sh -c /home/user/collect_and_run_uwsgi.sh"

    # Don't leave secrets lying around.
    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=15 \
        -o ConnectTimeout=5 \
        -i data-refinery-key.pem \
        "ubuntu@$API_IP_ADDRESS" "rm -f environment"
fi

echo "Deploy completed successfully."
