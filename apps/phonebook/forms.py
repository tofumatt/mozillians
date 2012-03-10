import re
from urlparse import urlparse

from django import forms
from django.conf import settings
from django.core.urlresolvers import resolve

import happyforms
import Image
from easy_thumbnails import processors
from product_details import product_details
from tower import ugettext as _, ugettext_lazy as _lazy

from groups.models import Group
from locations.models import Address, Country, PostalCode
from phonebook.models import Invite
from groups.models import Group
from users.models import User, UserProfile


PAGINATION_LIMIT = 20

REGEX_NUMERIC = re.compile('\d+', re.IGNORECASE)


class SearchForm(happyforms.Form):
    q = forms.CharField(widget=forms.HiddenInput, required=True)
    limit = forms.CharField(widget=forms.HiddenInput, required=False)
    nonvouched_only = forms.BooleanField(required=False)

    def clean_limit(self):
        """Validate that this limit is numeric and greater than 1"""
        limit = self.cleaned_data['limit']

        if not limit:
            limit = PAGINATION_LIMIT
        elif not REGEX_NUMERIC.match(str(limit)) or int(limit) < 1:
            limit = PAGINATION_LIMIT

        return limit


class UserForm(forms.ModelForm):
    """
    Instead of just inhereting form a UserProfile model form, this base class
    allows us to also abstract over methods that have to do with the User
    object that need to exist in both Registration and Profile.
    """

    first_name = forms.CharField(label=_lazy(u'First Name'), max_length=30,
                                                             required=False)
    last_name = forms.CharField(label=_lazy(u'Last Name'), max_length=30,
                                                           required=True)
    username = forms.CharField(label=_lazy(u'Nickname'), max_length=30,
                                                         required=False)

    def clean_username(self):
        username = self.cleaned_data['username']
        # If you don't submit a username, you aren't changing it so you're cool
        if not username:
            return None

        # Don't be jacking somebody's username
        # This causes a potential race condition however the worst that can
        # happen is bad UI.
        if (User.objects.filter(username=username) and
                username != self.instance.user.username):
            raise forms.ValidationError(_('This username is in use. Please try'
                                          ' another.'))

        # No funky characters in username
        if not re.match(r'^\w+$', username):
            raise forms.ValidationError(_('Please use only alphanumeric'
                                          ' characters'))

        if username not in settings.USERNAME_BLACKLIST:
            # TODO: we really should use middleware to handle the extra slashes
            # Check what can resolve the username (with/without trailing '/').
            # The last thing this can match for is profile.
            r = resolve(urlparse('/' + username)[2])
            # Check to make sure that only profile has been resolved for.
            if r.url_name == 'profile':
                return username

        raise forms.ValidationError(_('This username is reserved, please'
                                      ' choose another.'))

    def save(self, user):
        # First save the profile info.
        d = self.cleaned_data
        super(forms.ModelForm, self).save()

        # Then deal with the user info.
        d = self.cleaned_data
        user.first_name = d['first_name']
        user.last_name = d['last_name']
        if d['username']:
            user.username = d['username']
        user.save()


class ProfileForm(UserForm):
    photo = forms.ImageField(label=_lazy(u'Profile Photo'), required=False)
    photo_delete = forms.BooleanField(label=_lazy(u'Remove Profile Photo'),
                                      required=False)

    groups = forms.CharField(label=_lazy(u'Groups'), required=False)

    #: L10n: Street address; not entire address
    street = forms.CharField(label=_lazy(u'Address'), required=False)
    city = forms.CharField(label=_lazy(u'City'), required=False)
    # TODO: Add validation of states/provinces/etc. for known/large countries.
    province = forms.CharField(label=_lazy(u'Province/State'), required=False)
    postal_code = forms.CharField(label=_lazy(u'Postal/Zip Code'),
                                  required=False)

    class Meta:
        model = UserProfile
        fields = ('ircname', 'website', 'bio', 'photo')
        widgets = {
            'bio': forms.Textarea(),
        }

    def __init__(self, *args, **kwargs):
        """Add a locale-aware list of countries to the form."""
        locale = kwargs.get('locale', 'en-US')
        if kwargs.get('locale'):
            del kwargs['locale']

        super(ProfileForm, self).__init__(*args, **kwargs)

        self.fields['country'] = forms.ChoiceField(label=_lazy(u'Country'),
                required=False, choices=([['', '--']] +
                                         Country.localized_list(locale)))

    def clean_country(self):
        """Return a country object for the country selected (None if empty)."""
        if not self.cleaned_data['country']:
            return None

        country = Country.objects.filter(id=self.cleaned_data['country'])
        return country[0] if country else None

    def clean_groups(self):
        """Groups are saved in lowercase because it's easy and consistent."""
        if not re.match(r'^[a-zA-Z0-9 .:,-]*$', self.cleaned_data['groups']):
            raise forms.ValidationError(_(u'Groups can only contain '
                                           'alphanumeric characters, dashes, '
                                           'spaces.'))

        system_groups = [g.name for g in self.instance.groups.all()
                         if g.system]

        new_groups = [g.strip()
                      for g in self.cleaned_data['groups'].lower().split(',')
                      if g and ',' not in g]

        return system_groups + new_groups

    def save(self, request):
        """Save the data to profile."""
        self._save_groups(request)
        super(ProfileForm, self).save(request.user)

        # TODO: Not needed/put in ProfileForm?
        """
        profile = request.user.get_profile()
        profile.website = self.cleaned_data['website']

        address = request.user.address
        address.street = self.cleaned_data['street']
        address.city = self.cleaned_data['city']
        address.province = self.cleaned_data['province']
        address.country = self.cleaned_data['country']

        if self.cleaned_data['postal_code']:
            postal_code, created = PostalCode.objects.get_or_create(
                    code=self.cleaned_data['postal_code'])
            address.postal_code = postal_code
        else:
            address.postal_code = None

        address.save()
        profile.save()
        """

    def _process_location_data(self):
        """Process the location data sanely so it can be saved."""
        # self.
        pass

    def _save_groups(self, request):
        """Parse a string of (usually comma-demilited) groups and save them."""
        profile = request.user.get_profile()

        # Remove any non-system groups that weren't supplied in this list.
        profile.groups.remove(*[g for g in profile.groups.all()
                                if g.name not in self.cleaned_data['groups']
                                and not g.system])

        # Add/create the rest of the groups
        groups_to_add = []
        for g in self.cleaned_data['groups']:
            (group, created) = Group.objects.get_or_create(name=g)

            if not group.system:
                groups_to_add.append(group)

        profile.groups.add(*groups_to_add)


class VouchForm(happyforms.Form):
    """Vouching is captured via a user's id."""
    vouchee = forms.IntegerField(widget=forms.HiddenInput)


class InviteForm(happyforms.ModelForm):

    def clean_recipient(self):
        recipient = self.cleaned_data['recipient']

        if User.objects.filter(email=recipient).count() > 0:
            raise forms.ValidationError(_(u'You cannot invite someone who has '
                                            'already been vouched.'))
        return recipient

    def save(self, inviter):
        invite = super(InviteForm, self).save(commit=False)
        invite.inviter = inviter
        invite.save()
        return invite

    class Meta:
        model = Invite
        exclude = ('redeemer', 'inviter')
