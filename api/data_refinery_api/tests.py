from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from data_refinery_common.models import Experiment, Sample, Organism
from data_refinery_api.serializers import ( 
    ExperimentSerializer, 
    DetailedExperimentSerializer,
    SampleSerializer, 
    DetailedSampleSerializer,
    OrganismSerializer,
    PlatformSerializer,
    InstitutionSerializer,

	# Jobs
    SurveyJobSerializer,
    DownloaderJobSerializer,
    ProcessorJobSerializer
)

class SanityTestAllEndpoints(APITestCase):
    def setUp(self):
        # Saving this for if we have protected endpoints
        # self.superuser = User.objects.create_superuser('john', 'john@snow.com', 'johnpassword')
        # self.client.login(username='john', password='johnpassword')
        # self.user = User.objects.create(username="mike")

        experiment = Experiment()
        experiment.save()
        sample = Sample()
        sample.save()

        return

    def test_all_endpoints(self):
        response = self.client.get(reverse('experiments'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('experiments_detail', kwargs={'pk': '1'}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('samples'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('samples_detail', kwargs={'pk': '1'}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('organisms'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('platforms'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('institutions'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('jobs'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('survey_jobs'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('downloader_jobs'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('processor_jobs'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('stats'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse('api_root'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
