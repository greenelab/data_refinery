import requests
from typing import List

from data_refinery_models.models import (
    Batch,
    BatchKeyValue,
    SurveyJob,
    SurveyJobKeyValue,
    Organism
)
from data_refinery_foreman.surveyor.external_source import (
    ExternalSourceSurveyor,
    ProcessorPipeline
)

# Import and set logger
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EXPERIMENTS_URL = "https://www.ebi.ac.uk/arrayexpress/json/v3/experiments/"
SAMPLES_URL = EXPERIMENTS_URL + "{}/samples"


class ArrayExpressSurveyor(ExternalSourceSurveyor):
    def source_type(self):
        return "ARRAY_EXPRESS"

    def downloader_task(self):
        return ("data_refinery_workers.downloaders"
                + ".array_express.download_array_express")

    def determine_pipeline(self,
                           batch: Batch,
                           key_values: List[BatchKeyValue] = []):
        return ProcessorPipeline.AFFY_TO_PCL

    def survey(self, survey_job: SurveyJob):
        experiment_accession_code = (
            SurveyJobKeyValue
            .objects
            .get(survey_job_id=survey_job.id,
                 key__exact="experiment_accession_code")
            .value
        )

        experiment_request = requests.get(self.EXPERIMENTS_URL
                                          + experiment_accession_code)
        experiment = experiment_request.json()["experiments"]["experiment"][0]

        if len(experiment["arraydesign"]) > 1:
            logger.warn("Experiment %s has more than one arraydesign listed.",
                        experiment_accession_code)

        platform_accession_code = experiment["arraydesign"][0]["accession"]

        r = requests.get(self.SAMPLES_URL.format(experiment_accession_code))
        samples = r.json()["experiment"]["sample"]

        for sample in samples:
            if "file" not in sample:
                continue

            organism_name = "UNKNOWN"
            for characteristic in sample["characteristic"]:
                if characteristic["category"].upper() == "ORGANISM":
                    organism_name = characteristic["value"].upper()

            if organism_name == "UNKNOWN":
                logger.error("Sample from experiment %s "
                             + "did not specify the organism name.",
                             experiment_accession_code)
                organism_id = 0
            else:
                organism_id = Organism.get_id_for_name(organism_name)

            for sample_file in sample["file"]:
                if sample_file["type"] != "data":
                    continue

                self.handle_batch(Batch(
                    size_in_bytes=-1,  # Will have to be determined later
                    download_url=sample_file["url"],
                    raw_format=sample_file["name"].split(".")[-1],
                    processed_format="PCL",
                    platform_accession_code=platform_accession_code,
                    experiment_accession_code=experiment_accession_code,
                    organism_id=organism_id,
                    organism_name=organism_name,
                    experiment_title=experiment["name"],
                    release_date=experiment["releasedate"],
                    last_uploaded_date=experiment["lastupdatedate"],
                    name=sample_file["name"]
                ))
