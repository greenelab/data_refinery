from __future__ import absolute_import, unicode_literals
import string
from typing import Dict
import rpy2.robjects as ro
from rpy2.rinterface import RRuntimeError
from celery import shared_task
from celery.utils.log import get_task_logger
from data_refinery_workers.processors import utils
from data_refinery_common import file_management
import logging

logger = get_task_logger(__name__)


def _prepare_files(kwargs: Dict) -> Dict:
    """Moves the .CEL file from the raw directory to the temp directory

    Also adds the keys input_file and output_file to kwargs so
    everything is prepared for processing.
    """
    # Array Express processor jobs have only one batch per job.
    batch = kwargs["batches"][0]

    try:
        file_management.download_raw_file(batch)
    except Exception:
        logging.exception(("Exception caught while retrieving raw file "
                           "%s for batch %d during Processor Job #%d."),
                          file_management.get_raw_path(batch),
                          batch.id, kwargs["job_id"])
        failure_template = "Exception caught while retrieving raw file {}"
        kwargs["job"].failure_reason = failure_template.format(batch.name)
        kwargs["success"] = False
        return kwargs

    kwargs["input_file"] = file_management.get_temp_pre_path(batch)
    kwargs["output_file"] = file_management.get_temp_post_path(batch)
    return kwargs


def _determine_brainarray_package(kwargs: Dict) -> Dict:
    """Determines the right brainarray package to use for the file.

    Expects kwargs to contain the key 'input_file'. Adds the key
    'brainarray_package' to kwargs."""
    input_file = kwargs["input_file"]
    try:
        header = ro.r['::']('affyio', 'read.celfile.header')(input_file)
    except RRuntimeError as e:
        # Array Express processor jobs have only one batch per job.
        file_management.remove_temp_directory(kwargs["batches"][0])

        base_error_template = "unable to read Affy header in input file {0} due to error: {1}"
        base_error_message = base_error_template.format(input_file, str(e))
        log_message = "Processor Job %d running AFFY_TO_PCL pipeline " + base_error_message
        logger.error(log_message)
        kwargs["job"].failure_reason = base_error_message
        kwargs["success"] = False
        return kwargs

    # header is a list of vectors. [0][0] contains the package name.
    punctuation_table = str.maketrans(dict.fromkeys(string.punctuation))
    package_name = header[0][0].translate(punctuation_table).lower()

    # Headers can contain the version "v1" or "v2", which doesn't
    # appear in the brainarray package name. This replacement is
    # brittle, but the list of brainarray packages is relatively short
    # and we can monitor what packages are added to it and modify
    # accordingly. So far "v1" and "v2" are the only known versions
    # which must be accomodated in this way.
    package_name_without_version = package_name.replace("v1", "").replace("v2", "")
    kwargs["brainarray_package"] = package_name_without_version + "hsentrezgprobe"
    return kwargs


def _run_scan_upc(kwargs: Dict) -> Dict:
    """Processes an input CEL file to an output PCL file.

    Does so using the SCAN.UPC package's SCANfast method using R.
    Expects kwargs to contain the keys 'input_file', 'output_file',
    and 'brainarray_package'.
    """
    input_file = kwargs["input_file"]

    try:
        # Prevents:
        # RRuntimeWarning: There were 50 or more warnings (use warnings()
        # to see the first 50)
        ro.r("options(warn=1)")

        # It's necessary to load the foreach library before calling SCANfast
        # because it doesn't load the library before calling functions
        # from it.
        ro.r("library('foreach')")

        ro.r['::']('SCAN.UPC', 'SCANfast')(
            input_file,
            kwargs["output_file"],
            probeSummaryPackage=kwargs["brainarray_package"]
        )
    except RRuntimeError as e:
        base_error_template = "encountered error in R code while processing {0}: {1}"
        base_error_message = base_error_template.format(input_file, str(e))
        log_message = "Processor Job %d running AFFY_TO_PCL pipeline " + base_error_message
        logger.error(log_message, kwargs["job_id"])
        kwargs["job"].failure_reason = base_error_message
        kwargs["success"] = False
        return kwargs
    finally:
        # Array Express processor jobs have only one batch per job.
        file_management.remove_temp_directory(kwargs["batches"][0])

    return kwargs


@shared_task
def affy_to_pcl(job_id: int) -> None:
    utils.run_pipeline({"job_id": job_id},
                       [utils.start_job,
                        _prepare_files,
                        _determine_brainarray_package,
                        _run_scan_upc,
                        utils.upload_processed_files,
                        utils.cleanup_raw_files,
                        utils.end_job])
