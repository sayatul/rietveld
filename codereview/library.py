# Copyright 2008 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Django template library for Rietveld."""

import cgi
import logging

from google.appengine.api import memcache
from google.appengine.api import users

import django.template
import django.utils.safestring

import models

register = django.template.Library()


@register.filter
def show_user(email, arg=None, autoescape=None, memcache_results=None):
  """Render a link to the user's dashboard, with text being the nickname."""
  if isinstance(email, users.User):
    email = email.email()
  if not arg:
    user = users.get_current_user()
    if user is not None and email == user.email():
      return 'me'

  if memcache_results is not None:
    ret = memcache_results.get(email)
  else:
    ret = memcache.get('show_user:' + email)

  if ret is None:
    logging.debug('memcache miss for %r', email)
    account = models.Account.get_account_for_email(email)
    if account is not None and account.user_has_selected_nickname:
      ret = ('<a href="/user/%(key)s" onMouseOver="M_showUserInfoPopup(this)">'
             '%(key)s</a>' % {'key': cgi.escape(account.nickname)})
    else:
      # No account.  Let's not create a hyperlink.
      nick = email
      if '@' in nick:
        nick = nick.split('@', 1)[0]
      ret = cgi.escape(nick)

    memcache.add('show_user:%s' % email, ret, 300)

    # populate the dict with the results, so same user in the list later
    # will have a memcache "hit" on "read".
    if memcache_results is not None:
      memcache_results[email] = ret

  return django.utils.safestring.mark_safe(ret)


@register.filter
def show_users(email_list, arg=None):
  """Render list of links to each user's dashboard."""
  if not email_list:
    # Don't wast time calling memcache with an empty list.
    return ''
  memcache_results = memcache.get_multi(email_list, key_prefix='show_user:')
  return django.utils.safestring.mark_safe(', '.join(
      show_user(email, arg, memcache_results=memcache_results)
      for email in email_list))


class UrlAppendViewSettingsNode(django.template.Node):
  """Django template tag that appends context and column_width parameter.

  This tag should be used after any URL that requires view settings.

  Example:

    <a href='{%url /foo%}{%urlappend_view_settings%}'>

  The tag tries to get the current column width and context from the
  template context and if they're present it returns '?param1&param2'
  otherwise it returns an empty string.
  """

  def __init__(self):
    """Constructor."""
    self.view_context = django.template.Variable('context')
    self.view_colwidth = django.template.Variable('column_width')

  def render(self, context):
    """Returns a HTML fragment."""
    url_params = []

    current_context = -1
    try:
      current_context = self.view_context.resolve(context)
    except django.template.VariableDoesNotExist:
      pass
    if current_context is None:
      url_params.append('context=')
    elif isinstance(current_context, int) and current_context > 0:
      url_params.append('context=%d' % current_context)

    current_colwidth = None
    try:
      current_colwidth = self.view_colwidth.resolve(context)
    except django.template.VariableDoesNotExist:
      pass
    if current_colwidth is not None:
      url_params.append('column_width=%d' % current_colwidth)

    if url_params:
      return '?%s' % '&'.join(url_params)
    return ''

@register.tag
def urlappend_view_settings(parser, token):
  """The actual template tag."""
  return UrlAppendViewSettingsNode()


def get_nickname(email, never_me, request=None):
  """Return a nickname for an email address.

  If 'never_me' is True, 'me' is not returned if 'email' belongs to the
  current logged in user. If 'request' is a HttpRequest, it is used to
  cache the nickname returned by models.Account.get_nickname_for_email().
  """
  if isinstance(email, users.User):
    email = email.email()
  if not never_me:
    if request is not None:
      user = request.user
    else:
      user = users.get_current_user()
    if user is not None and email == user.email():
      return 'me'

  if request is None:
    logging.warn("request not found in template context.")
    return models.Account.get_nickname_for_email(email)
  else:
    if getattr(request, '_nicknames', None) is None:
      request._nicknames = {}
    if email in request._nicknames:
      return request._nicknames[email]
    result = models.Account.get_nickname_for_email(email)
    request._nicknames[email] = result
  return result


class NicknameNode(django.template.Node):
  """Renders a nickname for a given email address.

  The return value is cached if a HttpRequest is available in a
  'request' template variable.

  The template tag accepts one or two arguments. The first argument is
  the template variable for the email address. If the optional second
  argument evaluates to True, 'me' as nickname is never rendered.

  Example usage:
    {% cached_nickname msg.sender %}
    {% cached_nickname msg.sender True %}
  """

  def __init__(self, email_address, never_me=''):
    """Constructor.

    'email_address' is the name of the template variable that holds an
    email address. If 'never_me' evaluates to True, 'me' won't be returned.
    """
    self.email_address = django.template.Variable(email_address)
    self.never_me = bool(never_me.strip())
    self.is_multi = False

  def render(self, context):
    try:
      email = self.email_address.resolve(context)
    except django.template.VariableDoesNotExist:
      return ''
    request = context.get('request')
    if self.is_multi:
      return ', '.join(get_nickname(e, self.never_me, request) for e in email)
    return get_nickname(email, self.never_me, request)


@register.tag
def nickname(parser, token):
  """Almost the same as nickname filter but the result is cached."""
  try:
    tag_name, email_address, never_me = token.split_contents()
  except ValueError:
    try:
      tag_name, email_address = token.split_contents()
      never_me = ''
    except ValueError:
      raise django.template.TemplateSyntaxError(
        "%r requires exactly one or two arguments" % token.contents.split()[0])
  return NicknameNode(email_address, never_me)


@register.tag
def nicknames(parser, token):
  """Wrapper for nickname tag with is_multi flag enabled."""
  node = nickname(parser, token)
  node.is_multi = True
  return node
