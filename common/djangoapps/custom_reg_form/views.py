from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.core.urlresolvers import reverse
from django.utils.translation import ugettext as _
from django.contrib.auth import login

from edxmako.shortcuts import render_to_response

from .forms import SetNationalIdForm


def set_national_id(request):
    if request.method == 'POST':
        form = SetNationalIdForm(request.POST, user=request.user.is_authenticated() and request.user or None)
        if form.is_valid():
            form.save()
            messages.success(request, _('You National ID updated successfully. Now you can log in using it.'), extra_tags='set_national_id_success')
            return redirect(reverse('set_national_id'))
    elif request.method == 'GET':
        form = SetNationalIdForm(user=request.user.is_authenticated() and request.user or None)

    return render_to_response('set_national_id.html', {'form': form})
