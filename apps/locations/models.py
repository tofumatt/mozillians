from django.contrib.auth.models import User
from django.contrib.gis.db import models
from django.dispatch import receiver
from django.template.defaultfilters import slugify

from product_details import product_details

from locations.tasks import geocode_address


# Country keys are the same in all languages, so it doesn't matter which
# locale we use.
COUNTRIES = product_details.get_regions('en-US')


class Country(models.Model):
    """The largest container for a location (state, city, etc.).

    Our list of countries is populated and localized by the ``product_details``
    library. It is *not* a user-generated list and should not be modified by
    hand; let ``product_details`` take care of everything.

    If it turns out we're missing a country, we should address it upstream.
    """
    @classmethod
    def localized_list(cls, locale='en-US'):
        """A list of all countries in the DB, with their names localized."""
        regions = product_details.get_regions(locale)
        countries = [(c.id, regions[c.code]) for c in Country.objects.all()]
        return sorted(countries, key=lambda country: country[1])

    code = models.CharField(max_length=255, unique=True,
                            choices=COUNTRIES.items())

    #: We don't use SPATIAL indexes because we're using MySQL InnoDB tables.
    #: TODO: Add indexes when we go PostgreSQL by removing the
    #:       ``spatial_index=False`` argument.
    poly = models.PolygonField(null=True, spatial_index=False)

    def name(self, locale='en-US'):
        """Return the name of this country in the locale specified.

        Defaults to en-US if no locale is specified.
        """
        return product_details.get_regions(locale)[self.code]

    def __unicode__(self):
        """Return the name of this country in English."""
        return self.name()


class Address(models.Model):
    """An address is a user's full street address including country."""
    #: An address belongs to a User. They're created when a User is created.
    user = models.OneToOneField(User)

    street = models.CharField(max_length=200, null=True)
    city = models.CharField(max_length=150, null=True) # Bangkok, lol.
    province = models.CharField(max_length=200, null=True)
    postal_code = models.ForeignKey('PostalCode', null=True)
    country = models.ForeignKey('Country', null=True)

    #: We don't use SPATIAL indexes because we're using MySQL InnoDB tables.
    #: TODO: Add indexes when we go PostgreSQL.
    point = models.PointField(null=True, spatial_index=False)

    objects = models.GeoManager()

    def formatted(self, *args, **kwargs):
        """Return this address with only the specified attributes."""
        if not args:
            args = ['city', 'province', 'country']
        locale = kwargs.get('locale')

        address = ', '.join([unicode(getattr(self, a)) for a in args
                if a is not 'country' and getattr(self, a)])

        if self.country and locale:
            return '%s, %s' % (address, self.country.name(locale))
        elif self.country:
            return '%s, %s' % (address, self.country.name())
        else:
            return address

    def __unicode__(self):
        """Return the fully formatted contents of this address."""
        return self.formatted('street', 'city', 'province', 'postal_code',
                              'country')


class PostalCode(models.Model):
    code = models.CharField(max_length=50, null=True)
    #: We don't use SPATIAL indexes because we're using MySQL InnoDB tables.
    #: TODO: Add indexes when we go PostgreSQL.
    poly = models.PolygonField(null=True, spatial_index=False)

    objects = models.GeoManager()

    def __unicode__(self):
        """This object's unformatted postal/zip code.

        If you want to return the postal code formatted with "local flavour"
        (e.g. a Canadian postal code with a space between each three-character
        section), use the ``_format_for(locale)`` method.
        TODO: Write ``format_for(locale)`` method.
        """
        return self.code


@receiver(models.signals.post_save, sender=Address)
def geocode_after_save(sender, instance, created, **kwargs):
    """Queue a reverse-geocoding processing for an address."""
    geocode_address.apply_async(args=[instance.id])
