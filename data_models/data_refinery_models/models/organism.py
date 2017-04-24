import requests
import urllib
from xml.etree import ElementTree
from django.db import models
from data_refinery_models.models.base_models import TimeTrackedModel

# Import and set logger
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NCBI_ROOT_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
ESEARCH_URL = NCBI_ROOT_URL + "esearch.fcgi?db=taxonomy"
SCIENTIFIC_NAME_URL = ESEARCH_URL + "&field=scin"
EFETCH_URL = NCBI_ROOT_URL + "efetch.fcgi?db=taxonomy"


class UnscientifcNameError(BaseException):
    pass


class InvalidNCBITaxonomyId(BaseException):
    pass


def get_scientific_name(taxonomy_id: int):
    response = requests.get(EFETCH_URL + "&id=" + str(taxonomy_id))

    root = ElementTree.fromstring(response.text)
    taxon_list = root.findall("Taxon")

    if(len(taxon_list) == 0):
        logger.error("No names returned by ncbi.nlm.nih.gov for organism "
                     + "with taxonomy ID %d.",
                     taxonomy_id)
        raise InvalidNCBITaxonomyId

    return taxon_list[0].find("ScientificName").text


def get_taxonomy_id(organism_name: str):
    escaped_name = urllib.parse.quote(organism_name)
    response = requests.get(ESEARCH_URL + "&term=" + escaped_name)

    root = ElementTree.fromstring(response.text)
    id_list = root.find("IdList").findall("Id")

    if(len(id_list) == 0):
        logger.error("Unable to retrieve NCBI taxonomy ID number for organism "
                     + "with name: %s",
                     organism_name)
        return 0
    elif(len(id_list) > 1):
        logger.warn("Organism with name %s returned multiple NCBI taxonomy ID "
                    + "numbers.",
                    organism_name)

    return int(id_list[0].text)


def get_taxonomy_id_scientific(organism_name: str):
    escaped_name = urllib.parse.quote(organism_name)
    response = requests.get(SCIENTIFIC_NAME_URL + "&term=" + escaped_name)

    root = ElementTree.fromstring(response.text)
    id_list = root.find("IdList").findall("Id")

    if(len(id_list) == 0):
        raise UnscientifcNameError
    elif(len(id_list) > 1):
        logger.warn("Organism with name %s returned multiple NCBI taxonomy ID "
                    + "numbers.",
                    organism_name)

    return int(id_list[0].text)


class Organism(TimeTrackedModel):
    name = models.CharField(max_length=256)
    taxonomy_id = models.IntegerField()
    is_scientific_name = models.BooleanField(default=False)

    def get_name_for_id(self, taxonomy_id: int):
        try:
            organism = (self.objects
                        .filter(taxonomy_id=taxonomy_id)
                        .order_by("-is_scientific_name")
                        [0])
        except IndexError:
            name = get_scientific_name(taxonomy_id).upper()
            organism = Organism(name=name,
                                taxonomy_id=taxonomy_id,
                                is_scientific_name=True)
            organism.save()

        return organism.name

    def get_id_for_name(self, name: str):
        name = name.upper()
        try:
            organism = (self.objects
                        .filter(name=name)
                        [0])
        except IndexError:
            is_scientific_name = False
            try:
                taxonomy_id = get_taxonomy_id_scientific(name)
                is_scientific_name = True
            except UnscientifcNameError:
                taxonomy_id = get_taxonomy_id(name)

            organism = Organism(name=name,
                                taxonomy_id=taxonomy_id,
                                is_scientific_name=is_scientific_name)
            organism.save()

        return organism.taxonomy_id

    class Meta:
        db_table = "organisms"
