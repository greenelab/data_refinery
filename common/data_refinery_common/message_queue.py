"""Provides an interface to send messages to the Message Queue."""

from __future__ import absolute_import, unicode_literals
from enum import Enum
import nomad
from nomad.api.exceptions import URLNotFoundNomadException
from data_refinery_common.utils import get_env_variable
from data_refinery_common.job_lookup import ProcessorPipeline, Downloaders
from data_refinery_common.logging import get_and_configure_logger

logger = get_and_configure_logger(__name__)


# There are currently two Nomad Job Specifications defined in
# workers/downloader.nomad.tpl and workers/processor.nomad.tpl.
# These constants are the identifiers for those two job specifications.
NOMAD_TRANSCRIPTOME_JOB = "TRANSCRIPTOME_INDEX"
NOMAD_DOWNLOADER_JOB = "DOWNLOADER"


def send_job(job_type: Enum, job_id: int) -> None:
    """Queues a worker job by sending a Nomad Job dispatch message.

    job_type must be a valid Enum for ProcessorPipelines or
    Downloaders as defined in data_refinery_common.job_lookup.
    job_id must correspond to an existing ProcessorJob or
    DownloaderJob record.
    """
    nomad_host = get_env_variable("NOMAD_HOST")
    nomad_port = get_env_variable("NOMAD_PORT", "4646")
    nomad_client = nomad.Nomad(nomad_host, port=int(nomad_port), timeout=5)

    if job_type is ProcessorPipeline.TRANSCRIPTOME_INDEX_LONG \
       or job_type is ProcessorPipeline.TRANSCRIPTOME_INDEX_SHORT:
        nomad_job = NOMAD_TRANSCRIPTOME_JOB
    elif job_type is ProcessorPipeline.SALMON:
        nomad_job = ProcessorPipeline.SALMON.value
    elif job_type is ProcessorPipeline.AFFY_TO_PCL:
        nomad_job = ProcessorPipeline.AFFY_TO_PCL.value
    elif job_type is ProcessorPipeline.NO_OP:
        nomad_job = ProcessorPipeline.NO_OP.value
    elif job_type in list(Downloaders):
        nomad_job = NOMAD_DOWNLOADER_JOB
    else:
        raise ValueError("Invalid job_type.")

    logger.info("Queuing %s nomad job to run DR job %s with id %d.",
                nomad_job,
                job_type.value,
                job_id)
    try:
        nomad_client.job.dispatch_job(nomad_job, meta={"JOB_NAME": job_type.value,
                                                       "JOB_ID": str(job_id)})
    except URLNotFoundNomadException:
        logger.error("Dispatching Nomad job of type %s to host %s and port %s failed.",
                     job_type, nomad_host, nomad_port)
        raise
