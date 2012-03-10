import urllib
from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import models
from django.db.models import signals as dbsignals
from django.dispatch import receiver

from elasticutils import S
from elasticutils.models import SearchMixin
from funfactory.utils import absolutify
from funfactory.urlresolvers import reverse
from sorl.thumbnail import ImageField
from tower import ugettext as _, ugettext_lazy as _lazy

from locations.models import Address
from groups.models import Group
from locations.models import Address
from phonebook.models import get_random_string


class UserProfile(SearchMixin, models.Model):
    # This field is required.
    user = models.OneToOneField(User)

    # Other fields here
    confirmation_code = models.CharField(max_length=32, editable=False,
                                         unique=True)
    is_confirmed = models.BooleanField(default=False)
    is_vouched = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True, default=datetime.now)
    # If vouched because of Mozilla.* email, make note of it
    is_autovouched = models.BooleanField(default=False)
    website = models.URLField(max_length=200, verbose_name=_lazy(u'Website'),
                              default='', blank=True, null=True)

    # Foreign Keys and Relationships
    vouched_by = models.ForeignKey('UserProfile', null=True, default=None,
                                   on_delete=models.SET_NULL)

    groups = models.ManyToManyField('groups.Group')
    bio = models.CharField(max_length=255, verbose_name=_lazy(u'Bio'),
                                           default='', blank=True)
    photo = ImageField(default='', blank=True, upload_to='uploads/userprofile')
    display_name = models.CharField(max_length=255, default='', blank=True)
    ircname = models.CharField(max_length=63,
                               verbose_name=_lazy(u'IRC Nickname'),
                               default='', blank=True)

    class Meta:
        db_table = 'profile'

    def is_complete(self):
        """
        Tests if a user has all the information needed to move on past the
        original registration view
        """
        return self.display_name and self.display_name != ' '

    def vouch(self, vouched_by, system=True, commit=True):
        changed = system  # do we need to do a vouch?
        if system:
            self.is_vouched = True
            self.is_autovouched = True

        if vouched_by and vouched_by.is_vouched:
            changed = True
            self.is_vouched = True
            self.vouched_by = vouched_by

        if commit and changed:
            self.save()
            # Email the user and tell them they were vouched.
            self._email_now_vouched()

    def get_confirmation_url(self):
        url = (absolutify(reverse('confirm')) + '?code=' +
               self.confirmation_code)
        return url

    def get_send_confirmation_url(self):
        url = (reverse('send_confirmation') + '?' +
               urllib.urlencode({'user': self.user.username}))
        return url

    def _email_now_vouched(self):
        """Email this user, letting them know they are now vouched."""
        subject = _(u'You are now vouched on Mozillians!')
        message = _(u"You've now been vouched on Mozillians.org. "
                     "You'll now be able to search, vouch "
                     "and invite other Mozillians onto the site.")
        send_mail(subject, message, 'no-reply@mozillians.org',
                  [self.user.email])

    @property
    def full_name(self):
        return '%s %s' % (self.user.first_name, self.user.last_name)

    def __unicode__(self):
        """Return this user's name when their profile is called."""
        return self.display_name

    def fields(self):
        attrs = ('id', 'is_confirmed', 'is_vouched', 'website',
                 'bio', 'display_name', 'ircname')
        d = dict((a, getattr(self, a)) for a in attrs)
        # user data
        attrs = ('username', 'first_name', 'last_name', 'email', 'last_login',
                 'date_joined')
        d.update(dict((a, getattr(self.user, a)) for a in attrs))
        # Index group ids... for fun.
        groups = list(self.groups.values_list('name', flat=True))
        d.update(dict(groups=groups))
        return d

    @classmethod
    def search(cls, query, vouched=None):
        """Sensible default search for UserProfiles."""
        query = query.lower().strip()
        fields = ('first_name__text', 'last_name__text', 'display_name__text',
                  'username__text', 'bio__text', 'website__text',
                  'email__text', 'groups__text', 'first_name__startswith',
                  'last_name__startswith', 'ircname')
        q = dict((field, query) for field in fields)
        s = S(cls).query(or_=q)
        if vouched is not None:
            s = s.filter(is_vouched=vouched)
        return s

    def anonymize(self):
        """Remove personal info from a user"""

        for name in ['first_name', 'last_name']:
            setattr(self.user, name, '')

        self.user.save()

        for f in self._meta.fields:
            if not f.editable or f.name in ['id', 'user']:
                continue

            if f.default == models.fields.NOT_PROVIDED:
                raise Exception('No default value for %s' % f.name)

            setattr(self, f.name, f.default)

        for f in self._meta.many_to_many:
            getattr(self, f.name).clear()

        self.save()


@receiver(models.signals.post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    dn = '%s %s' % (instance.first_name, instance.last_name)

    if created:
        address = Address.objects.create(user=instance)
        UserProfile.objects.create(user=instance, display_name=dn)
    else:
        u = UserProfile.objects.get(user=instance)
        u.display_name = dn
        u.save()


@receiver(models.signals.pre_save, sender=UserProfile)
def generate_code(sender, instance, raw, using, **kwargs):
    if instance.confirmation_code:
        return

    code = get_random_string(32)
    while UserProfile.objects.filter(confirmation_code=code).count():
        code = get_random_string(32)

    instance.confirmation_code = code


@receiver(models.signals.pre_save, sender=UserProfile)
def auto_vouch(sender, instance, raw, using, **kwargs):
    """Auto vouch mozilla.com users."""
    if not instance.id:
        email = instance.user.email
        if any(email.endswith('@' + x) for x in settings.AUTO_VOUCH_DOMAINS):
            instance.vouch(None, system=True, commit=False)


@receiver(models.signals.post_save, sender=UserProfile)
def add_to_staff_group(sender, instance, created, **kwargs):
    """Add all mozilla.com users to the "staff" group upon creation."""
    if created:
        email = instance.user.email
        if (any(email.endswith('@' + x) for x in
                                               settings.AUTO_VOUCH_DOMAINS)):
            instance.groups.add(Group.objects.get(name='staff', system=True))


@receiver(dbsignals.post_save, sender=User)
@receiver(dbsignals.post_save, sender=UserProfile)
def update_search_index(sender, instance, **kw):
    from elasticutils import tasks
    tasks.index_objects.delay(UserProfile, [instance.id])


@receiver(dbsignals.post_delete, sender=UserProfile)
def remove_from_search_index(sender, instance, **kw):
    from elasticutils import tasks
    tasks.unindex_objects.delay(sender, [instance.id])
