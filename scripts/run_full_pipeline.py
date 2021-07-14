#!/usr/bin/env python3

import argparse
import json
import subprocess


def parse_args():
    description = """This script can be used to run the full pipeline.
    This includes surveyor jobs queuing downloader jobs, which in turn should queue processor jobs.
    This includes Microarray experiments, RNA-Seq experiments, and transcriptome indices.
    This script must be run from the top level directory of the refinebio repository.!"""
    parser = argparse.ArgumentParser(description=description)

    accession_help_text = """Specify the accession_code of the experiment to survey.
    If creating a Transcriptome Index an organism name like 'Homo
    Sapiens' should be supplied if the organism is in the main
    division of Ensembl. If the organism is in any other division it
    should be specified like 'Arabidopsis thaliana, EnsemblPlants'."""
    parser.add_argument("accession_code", help=accession_help_text)

    return parser.parse_args()


def get_job_to_run():
    completed_command = subprocess.run(
        ["./foreman/run_surveyor.sh", "get_job_to_be_run"], stdout=subprocess.PIPE,
    )

    # The JSON output is on the last line of the output, but it has a
    # newline as well.
    last_line = completed_command.stdout.decode("utf-8").split("\n")[-2].strip()
    if completed_command.returncode == 0:
        return json.loads(last_line)


def run_job(job):
    job_name = job["job_name"]
    job_id = job["job_id"]
    print(f"Running {job_name} Job with id {job_id}!")

    image_name = ""
    if job["job_type"] == "DownloaderJob":
        subcommand = "run_downloader_job"
        image_name = "downloaders"
    else:
        subcommand = "run_processor_job"

        if job_name == "AFFY_TO_PCL":
            image_name = "affymetrix"
        elif job_name == "SALMON":
            image_name = "salmon"
        elif job_name == "ILLUMINA_TO_PCL":
            image_name = "illumina"
        elif job_name in ["TRANSCRIPTOME_INDEX_LONG", "TRANSCRIPTOME_INDEX_SHORT"]:
            image_name = "transcriptome"
        elif job_name == "NO_OP":
            image_name = "no_op"

    subprocess.check_call(
        [
            "./workers/run_job.sh",
            "-i",
            image_name,
            subcommand,
            f"--job-name={job_name}",
            f"--job-id={job_id}",
        ]
    )


def survey_accession(accession_code):
    subprocess.check_call(
        ["./foreman/run_surveyor.sh", "survey_all", "--accession", accession_code]
    )


def run_full_pipeline(accession_code):
    survey_accession(accession_code)

    job_to_run = get_job_to_run()

    while job_to_run:
        run_job(job_to_run)

        job_to_run = get_job_to_run()


if __name__ == "__main__":
    args = parse_args()

    run_full_pipeline(args.accession_code)
