# SPDX-FileCopyrightText: © 2015-2022 Germar Reitze
# SPDX-FileCopyrightText: © 2008 Canonical Ltd.
# SPDX-FileCopyrightText: © 2004-2006 Red Hat Inc. <http://www.redhat.com>
# SPDX-FileCopyrightText: © 2005-2007 Collabora Ltd. <http://www.collabora.co.uk>
# SPDX-FileCopyrightText: © 2009 David D. Lowe
#
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-License-Identifier: MIT
# SPDX-License-Identifier: CC0-1.0
#
# This file is released under several licenses mentioned above. The file is
# part of the program "Back In Time". The program as a whole is released under
# GNU General Public License v2 (GPLv2). See file/folder LICENSE or go to
# - <https://spdx.org/licenses/GPL-2.0-or-later.html>.
# - <https://spdx.org/licenses/MIT.html>
# - <https://spdx.org/licenses/CC0-1.0.html>
#
# Note about the licenses by Christian Buhtz (2024-09):
# Despite extensive research and attempts to contact the aforementioned
# individuals and institutions, it was not possible to definitively determine
# which of the mentioned licenses and copyright notices apply to which parts of
# the code contained in this file. The situation could not be clarified even
# with the git commit history.
# It should be noted that, in case of doubt, preference should be given to the
# strongest or most restrictive license.
#
# Before SPDX meta data was added to the file it originally had some comments
# that are summarized as follows:
# - Germar Reitze claimed GPL-2.0-or-later in context of Back In Time.
# - Unknown person claimed GPL-2.0-or-later in context of "jockey".
# - Read Hat Inc. and Collabora Ltd. claimed MIT License in context of
#   "python-dbus-docs"
# - David D. Lowe claimed CC0-1.0 (public domain) in unknown context.
#
# Because of MIT License the following permission notice need to be included
# in this file and should not be removed:
# --- Begin of MIT License permission notice ---
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use, copy,
# modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
# --- End of MIT License permission notice ---
import os
import re
from subprocess import Popen, PIPE
try:
    import pwd
except ImportError:
    pwd = None

import dbus
import dbus.service
import dbus.mainloop
# pylint: disable-next=import-error,useless-suppression
import dbus.mainloop.pyqt6
# pylint: disable-next=import-error,useless-suppression
from dbus.mainloop.pyqt6 import DBusQtMainLoop
from PyQt6.QtCore import QCoreApplication

UDEV_RULES_PATH = '/etc/udev/rules.d/99-backintime-%s.rules'


class InvalidChar(dbus.DBusException):
    _dbus_error_name = 'net.launchpad.backintime.InvalidChar'


class InvalidCmd(dbus.DBusException):
    _dbus_error_name = 'net.launchpad.backintime.InvalidCmd'


class LimitExceeded(dbus.DBusException):
    _dbus_error_name = 'net.launchpad.backintime.LimitExceeded'


class PermissionDeniedByPolicy(dbus.DBusException):
    _dbus_error_name = 'com.ubuntu.DeviceDriver.PermissionDeniedByPolicy'


class UdevRules(dbus.service.Object):
    def __init__(self, conn=None, object_path=None, bus_name=None):
        super(UdevRules, self).__init__(conn, object_path, bus_name)

        # the following variables are used by _checkPolkitPrivilege
        self.polkit = None
        self.enforce_polkit = True

        self.tmpDict = {}

        # find su path
        self.su = self._which('su', '/bin/su')
        self.backintime = self._which('backintime', '/usr/bin/backintime')
        self.nice = self._which('nice', '/usr/bin/nice')
        self.ionice = self._which('ionice', '/usr/bin/ionice')
        self.max_rules = 100
        self.max_users = 20
        self.max_cmd_len = 120  # was 100 before but was too small (see #1027)

    def _which(self, exe, fallback):
        proc = Popen(['which', exe], stdout=PIPE)
        ret = proc.communicate()[0].strip().decode()
        if proc.returncode or not ret:
            return fallback

        return ret

    def _validateCmd(self, cmd):

        if cmd.find("&&") != -1:
            raise InvalidCmd("Parameter 'cmd' contains '&&' concatenation")
        # make sure it starts with an absolute path
        elif not cmd.startswith(os.path.sep):
            raise InvalidCmd("Parameter 'cmd' does not start with '/'")

        parts = cmd.split()

        # make sure only well known commands and switches are used
        whitelist = (
            (self.nice, r'^-n'),
            (self.ionice, r'(^-c|^-n)'),
        )

        while parts:
            for c, switches in whitelist:
                if parts[0] == c:
                    parts.pop(0)
                    while parts and re.match(switches, parts[0]):
                        parts.pop(0)
                    break
            else:
                break

        if not parts:
            raise InvalidCmd(
                "Parameter 'cmd' does not contain the backintime command")

        elif parts[0] != self.backintime:
            raise InvalidCmd("Parameter 'cmd' contains non-whitelisted "
                             f"cmd/parameter ({parts[0]})")

    def _checkLimits(self, owner, cmd):

        if len(self.tmpDict.get(owner, [])) >= self.max_rules:
            raise LimitExceeded("Maximum number of cached rules reached (%d)"
                            % self.max_rules)

        elif len(self.tmpDict) >= self.max_users:
            raise LimitExceeded("Maximum number of cached users reached (%d)"
                            % self.max_users)

        elif len(cmd) > self.max_cmd_len:
            raise LimitExceeded("Maximum length of command line reached (%d)"
                            % self.max_cmd_len)

    @dbus.service.method("net.launchpad.backintime.serviceHelper.UdevRules",
                         in_signature='ss', out_signature='',
                         sender_keyword='sender', connection_keyword='conn')
    def addRule(self, cmd, uuid, sender=None, conn=None):
        """
        Receive command and uuid and create an Udev rule out of this.
        This is done on the service side to prevent malicious code to
        run as root.
        """
        # prevent breaking out of su command
        chars = re.findall(r'[^a-zA-Z0-9-/\.>& ]', cmd)
        if chars:
            raise InvalidChar("Parameter 'cmd' contains invalid character(s) %s"
                              % '|'.join(set(chars)))
        # only allow relevant chars in uuid
        chars = re.findall(r'[^a-zA-Z0-9-]', uuid)
        if chars:
            raise InvalidChar("Parameter 'uuid' contains invalid character(s) %s"
                              % '|'.join(set(chars)))

        self._validateCmd(cmd)

        info = SenderInfo(sender, conn)
        user = info.connectionUnixUser()
        owner = info.nameOwner()

        self._checkLimits(owner, cmd)

        #create su command
        sucmd = "%s - '%s' -c '%s'" %(self.su, user, cmd)
        #create Udev rule
        rule = 'ACTION=="add|change", ENV{ID_FS_UUID}=="%s", RUN+="%s"\n' %(uuid, sucmd)

        #store rule
        if not owner in self.tmpDict:
            self.tmpDict[owner] = []

        self.tmpDict[owner].append(rule)

    @dbus.service.method("net.launchpad.backintime.serviceHelper.UdevRules",
                         in_signature='', out_signature='b',
                         sender_keyword='sender', connection_keyword='conn')
    def save(self, sender=None, conn=None):
        """
        Save rules to destination file after user authenticated as admin.
        This will first check if there are any changes between
        temporary added rules and current rules in destination file.
        Returns False if files are identical or no rules to be installed.
        """
        info = SenderInfo(sender, conn)
        user = info.connectionUnixUser()
        owner = info.nameOwner()

        #delete rule if no rules in tmp
        if not owner in self.tmpDict or not self.tmpDict[owner]:
            self.delete(sender, conn)
            return False

        #return False if rule already exist.
        if os.path.exists(UDEV_RULES_PATH % user):
            with open(UDEV_RULES_PATH % user, 'r') as f:
                if self.tmpDict[owner] == f.readlines():
                    self._clean(owner)
                    return False

        #auth to save changes
        self._checkPolkitPrivilege(sender, conn, 'net.launchpad.backintime.UdevRuleSave')

        with open(UDEV_RULES_PATH % user, 'w') as f:
            f.writelines(self.tmpDict[owner])

        self._clean(owner)

        return True

    @dbus.service.method("net.launchpad.backintime.serviceHelper.UdevRules",
                         in_signature='', out_signature='',
                         sender_keyword='sender', connection_keyword='conn')
    def delete(self, sender=None, conn=None):
        """
        Delete existing Udev rule
        """
        info = SenderInfo(sender, conn)
        user = info.connectionUnixUser()
        owner = info.nameOwner()
        self._clean(owner)

        if os.path.exists(UDEV_RULES_PATH % user):
            #auth to delete rule
            self._checkPolkitPrivilege(sender, conn, 'net.launchpad.backintime.UdevRuleDelete')
            os.remove(UDEV_RULES_PATH % user)

    @dbus.service.method("net.launchpad.backintime.serviceHelper.UdevRules",
                         in_signature='', out_signature='',
                         sender_keyword='sender', connection_keyword='conn')
    def clean(self, sender=None, conn=None):
        """
        clean up previous cached rules
        """
        info = SenderInfo(sender, conn)
        self._clean(info.nameOwner())

    def _clean(self, owner):
        if owner in self.tmpDict:
            del self.tmpDict[owner]

    def _initPolkit(self):
        if self.polkit is None:
            self.polkit = dbus.Interface(dbus.SystemBus().get_object(
                'org.freedesktop.PolicyKit1',
                '/org/freedesktop/PolicyKit1/Authority', False),
                'org.freedesktop.PolicyKit1.Authority')

    def _checkPolkitPrivilege(self, sender, conn, privilege):
        # from jockey
        """
        Verify that sender has a given PolicyKit privilege.

        sender is the sender's (private) D-BUS name, such as ":1:42"
        (sender_keyword in @dbus.service.methods). conn is
        the dbus.Connection object (connection_keyword in
        @dbus.service.methods). privilege is the PolicyKit privilege string.

        This method returns if the caller is privileged, and otherwise throws a
        PermissionDeniedByPolicy exception.
        """
        if sender is None and conn is None:
            # called locally, not through D-BUS
            return
        if not self.enforce_polkit:
            # that happens for testing purposes when running on the session
            # bus, and it does not make sense to restrict operations here
            return

        # query PolicyKit
        self._initPolkit()

        try:
            # We don't need is_challenge return here, since we call
            # with AllowUserInteraction
            (is_auth, _, details) = self.polkit.CheckAuthorization(
                (
                    'system-bus-name',
                    {'name': dbus.String(sender, variant_level=1)}
                ),
                privilege,
                {'': ''},
                dbus.UInt32(1),
                '',
                timeout=3000
            )

        except dbus.DBusException as e:
            if e._dbus_error_name == 'org.freedesktop.DBus.Error.ServiceUnknown':
                # polkitd timed out, connect again
                self.polkit = None

                return self._checkPolkitPrivilege(sender, conn, privilege)

            else:
                raise

        if not is_auth:
            raise PermissionDeniedByPolicy(privilege)


class SenderInfo:
    def __init__(self, sender, conn):
        self.sender = sender
        self.dbus_info = dbus.Interface(conn.get_object('org.freedesktop.DBus',
                '/org/freedesktop/DBus/Bus', False), 'org.freedesktop.DBus')

    def connectionUnixUser(self):
        uid = self.dbus_info.GetConnectionUnixUser(self.sender)
        if pwd:
            return pwd.getpwuid(uid).pw_name
        else:
            return uid

    def nameOwner(self):
        return self.dbus_info.GetNameOwner(self.sender)

    def connectionPid(self):
        return self.dbus_info.GetConnectionUnixProcessID(self.sender)


if __name__ == '__main__':
    DBusQtMainLoop(set_as_default=True)

    app = QCoreApplication([])

    bus = dbus.SystemBus()
    name = dbus.service.BusName("net.launchpad.backintime.serviceHelper", bus)
    object = UdevRules(bus, '/UdevRules')

    print("Running BIT service.")
    app.exec()
