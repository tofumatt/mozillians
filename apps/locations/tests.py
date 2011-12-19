from time import sleep

from django import test
from django.contrib.auth.models import User

import test_utils
from funfactory.urlresolvers import reverse
from nose.tools import eq_
from pyquery import PyQuery as pq

from locations.models import Country
from phonebook.tests import LDAPTestCase, MOZILLIAN, mozillian_client


class TestLocations(LDAPTestCase):

    def test_empty_location_doesnt_appear(self):
        """Verify an empty location doesn't appear in a user's profile."""
        client = mozillian_client(MOZILLIAN['email'])

        canada, created = Country.objects.get_or_create(code='ca')
        r = client.post(reverse('phonebook.edit_profile'),
                        dict(last_name='Picnicface'), follow=True)
        doc = pq(r.content)

        assert doc('body#body-profile'), ('User should be on their profile '
                                          'page after submitting the form.')
        assert not doc('dt.location'), 'Location info should not appear.'
    
    def test_real_location_appears_in_profile_and_geocodes(self):
        """Verify a valid location appears in a user's profile/is geocoded."""
        client = mozillian_client(MOZILLIAN['email'])

        canada, created = Country.objects.get_or_create(code='ca')

        r = client.post(reverse('phonebook.edit_profile'),
                        dict(last_name='tofumatt', city='Montreal',
                             province='QC', country=canada.id),
                        follow=True)
        doc = pq(r.content)

        assert doc('body#body-profile'), ('User should be on their profile '
                                          'page after submitting the form.')
        assert doc('dt.location'), 'Location info should appear in the page.'
        eq_('Montreal, QC, Canada', doc('dd.location').text(), (
                'Location data should appear as submitted.'))
        
        user = User.objects.get(username=MOZILLIAN['email'])
        assert user.address.point is not None, (
                "User's address should be geolocated and have an associated "
                'POINT object stored in the database.')

    def test_bad_location_appears_in_profile_and_doesnt_geocode(self):
        """Verify any entered location appears in a user's profile."""
        client = mozillian_client(MOZILLIAN['email'])

        r = client.post(reverse('phonebook.edit_profile'),
                        dict(last_name='Luedecke', city='Hinterland Noplace',
                             province='XB'),
                        follow=True)
        doc = pq(r.content)

        assert doc('body#body-profile'), ('User should be on their profile '
                                          'page after submitting the form.')
        assert doc('dt.location'), 'Location info should appear in the page.'
        eq_('Hinterland Noplace, XB', doc('dd.location').text(), (
                'Location data should appear as submitted.'))
        
        user = User.objects.get(username=MOZILLIAN['email'])
        assert user.address.point is None, (
                "User's address should not be geolocated.")
    
    def test_location_can_be_removed(self):
        """Verify a user can delete their location and geo information."""
        client = mozillian_client(MOZILLIAN['email'])

        canada, created = Country.objects.get_or_create(code='ca')

        user = User.objects.get(username=MOZILLIAN['email'])
        address = user.address
        address.city = 'Halifax'
        address.province = 'NS'
        address.country = canada
        address.save()

        r = client.post(reverse('profile', args=[MOZILLIAN['uniq_id']]))
        doc = pq(r.content)

        assert doc('dt.location'), 'Location info should appear in the page.'
        eq_('Halifax, NS, Canada', doc('dd.location').text(), (
                'Location data should appear as submitted.'))

        user = User.objects.get(username=MOZILLIAN['email'])
        assert user.address.point is not None, (
                "User's address should be geolocated and have an associated "
                'POINT object stored in the database.')

        r = client.post(reverse('phonebook.edit_profile'),
                        dict(last_name='Luedecke', city='',
                             province='', country=''),
                        follow=True)
        doc = pq(r.content)

        assert doc('body#body-profile'), ('User should be on their profile '
                                          'page after submitting the form.')
        assert not doc('dt.location'), (
                "Location info should no longer appear on the user's profile.")
        
        user = User.objects.get(username=MOZILLIAN['email'])
        assert user.address.point is None, (
                "User's address should no longer be geolocated.")
