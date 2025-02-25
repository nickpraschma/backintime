# SPDX-FileCopyrightText: © 2008-2022 Oprea Dan
# SPDX-FileCopyrightText: © 2008-2022 Bart de Koning
# SPDX-FileCopyrightText: © 2008-2022 Richard Bailey
# SPDX-FileCopyrightText: © 2008-2022 Germar Reitze
# SPDX-FileCopyrightText: © 2008-2022 Taylor Raack
# SPDX-FileCopyrightText: © 2024 Christian Buhtz <c.buhtz@posteo.jp>
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This file is part of the program "Back In Time" which is released under GNU
# General Public License v2 (GPLv2). See file/folder LICENSE or go to
# <https://spdx.org/licenses/GPL-2.0-or-later.html>.
"""Collection of helper functions not fitting to other modules.
"""
import os
import sys
import pathlib
import subprocess
import shlex
import signal
import re
import errno
import gzip
import locale
import gettext
import hashlib
import ipaddress
from datetime import datetime, timedelta
from packaging.version import Version
from typing import Union
from bitbase import TimeUnit
import logger

# Try to import keyring
is_keyring_available = False
try:
    # Jan 4, 2024 aryoda: The env var BIT_USE_KEYRING is neither documented
    #                     anywhere nor used at all in the code.
    #                     Via "git blame" I have found a commit message saying:
    #                     "block subsequent 'import keyring' if it failed once"
    #                     So I assume it is an internal temporary env var only.
    # Note: os.geteuid() is used instead of tools.isRoot() here
    #       because the latter is still not available here in the global
    #       module code.
    if os.getenv('BIT_USE_KEYRING', 'true') == 'true' and os.geteuid() != 0:
        import keyring
        from keyring import backend
        import keyring.util.platform_
        is_keyring_available = True
except Exception as e:
    is_keyring_available = False
    # block subsequent 'import keyring' if it failed once before
    os.putenv('BIT_USE_KEYRING', 'false')
    logger.warning(f"'import keyring' failed with: {repr(e)}")

# getting dbus imports to work in Travis CI is a huge pain
# use conditional dbus import
ON_TRAVIS = os.environ.get('TRAVIS', 'None').lower() == 'true'
ON_RTD = os.environ.get('READTHEDOCS', 'None').lower() == 'true'

try:
    import dbus
except ImportError:
    if ON_TRAVIS or ON_RTD:
        # python-dbus doesn't work on Travis yet.
        dbus = None
    else:
        raise

import configfile
import bcolors
from exceptions import Timeout, InvalidChar, InvalidCmd, LimitExceeded, PermissionDeniedByPolicy
import languages

# Workaround:
# While unittesting and without regular invocation of BIT the GNU gettext
# class-based API isn't setup yet.
try:
    _('Warning')
except NameError:
    _ = lambda val: val

DISK_BY_UUID = '/dev/disk/by-uuid'

# |-----------------|
# | Handling paths  |
# |-----------------|


def sharePath():
    """Get path where Back In Time is installed.

    This is similar to ``XDG_DATA_DIRS``. If running from source return
    default ``/usr/share``.

    Share path like: ::

        /usr/share
        /usr/local/share
        /opt/usr/share

    Returns:
        str: Share path.
    """
    share = os.path.abspath(
        os.path.join(__file__, os.pardir, os.pardir, os.pardir)
    )

    if os.path.basename(share) == 'share':
        return share

    return '/usr/share'


def backintimePath(*path):
    """
    Get path inside ``backintime`` install folder.

    Args:
        *path (str): Paths that should be joined to ``backintime``.

    Returns:
        str: Child path of ``backintime`` child path e.g.
            ``/usr/share/backintime/common``or ``/usr/share/backintime/qt``.
    """
    return os.path.abspath(os.path.join(__file__, os.pardir, os.pardir, *path))


def docPath():
    """Not sure what this path is about.
    """
    path = pathlib.Path(sharePath()) / 'doc' / 'backintime-common'

    # Dev note (buhtz, aryoda, 2024-02):
    # This piece of code originally resisted in Config.__init__() and was
    # introduced by Dan in 2008. The reason for the existence of this "if"
    # is unclear.

    # Makefile (in common) does only install into share/doc/backintime-common
    # but never into the the backintime "binary" path so I guess the if is
    # a) either a distro-specific exception for a distro package that
    # (manually?) installs the LICENSE into another path
    # b) or a left-over from old code where the LICENSE was installed
    # differently...

    license_file = pathlib.Path(backintimePath()) / 'LICENSE'
    if license_file.exists():
        path = backintimePath()

    return str(path)


# |---------------------------------------------------|
# | Internationalization (i18n) & localization (L10n) |
# |---------------------------------------------------|
_GETTEXT_DOMAIN = 'backintime'
_GETTEXT_LOCALE_DIR = pathlib.Path(sharePath()) / 'locale'


def _determine_current_used_language_code(translation, language_code):
    """Return the language code used by GNU gettext for real.

    Args:
        translation(gettext.NullTranslations): The translation installed.
        language_code(str): Configured language code.

    The used language code can differ from the one in Back In Times config
    file and from the current systems locale.

    It is necessary because of situations where the language is not explicit
    setup in Back In Time config file and GNU gettext do try to find and use a
    language file for the current systems locale. But even this can fail and
    the fallback (source language "en") is used or an alternative locale.
    """

    try:
        # The field "language" is rooted in header of the po-file.
        current_used_language_code = translation.info()['language']

    except KeyError:
        # Workaround:
        # BIT versions 1.3.3 or older don't have the "language" field in the
        # header of their po-files.

        # The approach is to extract the language code from the full filepath
        # of the currently used mo-file.

        # Get the filepath of the used mo-file
        mo_file_path = gettext.find(
            domain=_GETTEXT_DOMAIN,
            localedir=_GETTEXT_LOCALE_DIR,
            languages=[language_code, ] if language_code else None,
        )

        # Extract the language code form that path
        if mo_file_path:
            mo_file_path = pathlib.Path(mo_file_path)
            # e.g /usr/share/locale/de/LC_MESSAGES/backintime.mo
            #                       ^^
            current_used_language_code = mo_file_path.relative_to(
                _GETTEXT_LOCALE_DIR).parts[0]

        else:
            # Workaround: Happens when LC_ALL=C, which in BIT context mean
            # its source language in English.
            current_used_language_code = 'en'

    return current_used_language_code


def initiate_translation(language_code):
    """Initiate Class-based API of GNU gettext.

    Args:
        language_code(str): Language code to use (based on ISO-639).

    It installs the ``_()`` (and ``ngettext()`` for plural forms)  in the
    ``builtins`` namespace and eliminates the need to ``import gettext``
    and declare ``_()`` in each module. The systems current local is used
    if the language code is None.
    """

    if language_code:
        logger.debug(f'Language code "{language_code}".')
    else:
        logger.debug('No language code. Use systems current locale.')

    translation = gettext.translation(
        domain=_GETTEXT_DOMAIN,
        localedir=_GETTEXT_LOCALE_DIR,
        languages=[language_code, ] if language_code else None,
        fallback=True
    )
    translation.install(names=['ngettext'])

    used_code = _determine_current_used_language_code(
        translation, language_code)

    set_lc_time_by_language_code(used_code)

    return used_code


def set_lc_time_by_language_code(language_code: str):
    """Set ``LC_TIME`` based on a specific language code.

    Args:
        language_code(str): A language code consisting of two letters.

    The reason is to display correctly translated weekday and months
    names. Python's :mod:`datetime` module, as well
    ``PyQt6.QtCore.QDate``, use :mod:`locale` to determine the
    correct translation. The module :mod:`gettext` and
    ``PyQt6.QtCore.QTranslator`` is not involved so their setup does
    not take effect.

    Be aware that a language code (e.g. ``de``) is not the same as a locale code
    (e.g. ``de_DE.UTF-8``). This function attempts to determine the latter based
    on the language code. A warning is logged if it is not possible.
    """

    # Determine the normalized locale code (e.g. "de_DE.UTF-8") by
    # language code (e.g. "de").

    # "de" -> "de_DE.ISO8859-1" -> "de_DE"
    code = locale.normalize(language_code).split('.')[0]

    try:
        # "de_DE" -> "de_DE.UTF-8"
        code = code + '.' + locale.getencoding()
    except AttributeError:  # Python 3.10 or older
        code = code + '.' + locale.getpreferredencoding()

    try:
        # logger.debug(f'Try to set locale.LC_TIME to "{code}" based on '
        #              f'language code "{language_code}".')
        locale.setlocale(locale.LC_TIME, code)

    except locale.Error:
        logger.warning(
            f'Determined normalized locale code "{code}" (from language code '
            f'"{language_code}") not available (or invalid). The code will be '
            'ignored. This might lead to unusual display of dates and '
            'timestamps, but it does not affect the functionality of the '
            f'application. Used locale is "{locale.getlocale()}".')


def get_available_language_codes():
    """Return language codes available in the current installation.

    The filesystem is searched for ``backintime.mo`` files and the language
    code is extracted from the full path of that files.

    Return:
        List of language codes.
    """

    # full path of one mo-file
    # e.g. /usr/share/locale/de/LC_MESSAGES/backintime.mo
    mo = gettext.find(domain=_GETTEXT_DOMAIN, localedir=_GETTEXT_LOCALE_DIR)

    if mo:
        mo = pathlib.Path(mo)
    else:
        # Workaround. This happens if LC_ALL=C and BIT don't use an explicit
        # language. Should be re-design.
        mo = _GETTEXT_LOCALE_DIR / 'xy' / 'LC_MESSAGES' / 'backintime.mo'

    # e.g. de/LC_MESSAGES/backintime.mo
    mo = mo.relative_to(_GETTEXT_LOCALE_DIR)

    # e.g. */LC_MESSAGES/backintime.mo
    mo = pathlib.Path('*') / pathlib.Path(*mo.parts[1:])

    mofiles = _GETTEXT_LOCALE_DIR.rglob(str(mo))

    return [p.relative_to(_GETTEXT_LOCALE_DIR).parts[0] for p in mofiles]


def get_language_names(language_code):
    """Return a list with language names in three different flavors.

    Language codes from `get_available_language_codes()` are combined with
    `languages.language_names` to prepare the list.

    Args:
        language_code (str): Usually the current language used by Back In Time.

    Returns:
        A dictionary indexed by language codes with 3-item tuples as
        values. Each tuple contain three representations of the same language:
        ``language_code`` (usually the current locales language),
        the language itself (native) and in English (the source language);
        e.g. ``ja`` (Japanese) for ``de`` (German) locale
        is ``('Japanisch', '日本語', 'Japanese')``.
    """
    result = {}
    codes = ['en'] + get_available_language_codes()

    for c in codes:

        try:
            # A dict with one specific language and how its name is
            # represented in all other languages.
            # e.g. "Japanese" in "de" is "Japanisch"
            # e.g. "Deutsch" in "es" is "alemán"
            lang = languages.names[c]

        except KeyError:
            names = None

        else:
            names = (
                # in currents locale language
                lang[language_code],
                # native
                lang['_native'],
                # in English (source language)
                lang['en']
            )

        result[c] = names

    return result


def get_native_language_and_completeness(language_code):
    """Return the language name in its native flavor and the completeness of
    its translation in percent.

    Args:
        language_code(str): The language code.

    Returns:
        A two-entry tuple with language name as string and a percent as
        integer.
    """
    name = languages.names[language_code][language_code]
    completeness = languages.completeness[language_code]

    return (name, completeness)

# |---------------------------------------|
# | Snapshot handling                     |
# |                                       |
# | Candidates for refactoring and moving |
# | into better suited modules/classes    |
# |---------------------------------------|

NTFS_FILESYSTEM_WARNING = _(
    'The destination filesystem for {path} is formatted with NTFS, which has '
    'known incompatibilities with Unix-style filesystems.')


def validate_and_prepare_snapshots_path(
        path: Union[str, pathlib.Path],
        host_user_profile: tuple[str, str, str],
        mode: str,
        copy_links: bool,
        error_handler: callable) -> bool:
    """Check if the given path is valid for being a snapshot path.

    It is checked if it is a folder, if it is writable, if the filesystem is
    supported and several other things.

    Dev note  (buhtz, 2024-09): That code is a good candidate to get moved
        into a class or module.

    Args:
        path: The path to validate as a snapshot path.
        host_user_profile: I three item list containing the values for 'host',
            'user' and 'profile' used as additional components for the
            snapshots path.
        mode: The profiles mode.
        copy_links: The copy links value.
        error_handler: Handle function receiving error messages.

    Returns: Success (`True`) or failure (`False`).
    """
    path = pathlib.Path(path)

    if not path.is_dir():
        error_handler(_('Invalid option. {path} is not a folder.')
                      .format(path=path))
        return False

    # build full path
    # <path>/backintime/<host>/<user>/<profile_id>
    full_path = pathlib.Path(path, 'backintime', *host_user_profile)

    # create full_path
    try:
        full_path.mkdir(mode=0o777, parents=True, exist_ok=True)

    except PermissionError:
        error_handler('\n'.join([
            _('Creation of following folder failed:'),
            str(full_path),
            _(f'Write access may be restricted.')]))
        return False

    # Test filesystem
    rc, msg = is_filesystem_valid(
        full_path, path, mode, copy_links)
    if msg:
        error_handler(msg)
    if rc is False:
        return False

    # Test write access for the folder
    rc, msg = is_writeable(full_path)
    if msg:
        error_handler(msg)
    if rc is False:
        return False

    return True


def is_filesystem_valid(full_path, msg_path, mode, copy_links):
    """
    Args:
        full_path: The path to validate.
        msg_path: The path used for display in error messages.
        mode: Snapshot profile mode.
        copy_links: Snapshot profiles copy links setting.

    Returns:
        (bool, str): A boolean value indicating success or failure and a
            msg string.

    """
    fs = filesystem(full_path if isinstance(full_path, str) else str(full_path))

    msg = None

    if fs == 'vfat':
        msg = _(
            "Destination filesystem for {path} is formatted with FAT "
            "which doesn't support hard-links. "
            "Please use a native Linux filesystem.").format(path=msg_path)

        return False, msg

    elif fs.startswith('ntfs'):
        msg = NTFS_FILESYSTEM_WARNING.format(path=msg_path)

    elif fs == 'cifs' and not copy_links:
        msg = _(
            'Destination filesystem for {path} is an SMB-mounted share. '
            'Please make sure the remote SMB server supports symlinks or '
            'activate {copyLinks} in {expertOptions}.') \
            .format(path=msg_path,
                    copyLinks=_('Copy links (dereference symbolic links)'),
                    expertOptions=_('Expert Options'))

    elif fs == 'fuse.sshfs' and mode not in ('ssh', 'ssh_encfs'):
        msg = _(
            "Destination filesystem for {path} is an sshfs-mounted share."
            " Sshfs doesn't support hard-links. "
            "Please use mode 'SSH' instead.").format(path=msg_path)

        return False, msg

    return True, msg


def is_writeable(folder):
    """Test write access for the folder.

    Args:
        folder: The folder to check.

    Returns:
        (bool, str): A boolean value indicating success or failure and a
            msg string.
    """

    folder = pathlib.Path(folder)

    check_path = folder / 'check'

    try:
        check_path.mkdir(
            # Do not create parent folders
            parents=False,
            # Raise error if exists
            exist_ok=False
        )

    except PermissionError:
        msg = '\n'.join([
            _('File creation failed in this folder:'),
            str(folder),
            _('Write access may be restricted.')])
        return False, msg

    else:
        check_path.rmdir()

    return True, None


# |------------------------------------|
# | Miscellaneous, not categorized yet |
# |------------------------------------|
def registerBackintimePath(*path):
    """
    Add BackInTime path ``path`` to :py:data:`sys.path` so subsequent imports
    can discover them.

    Args:
        *path (str):    paths that should be joined to 'backintime'

    Note:
        Duplicate in :py:func:`qt/qttools.py` because modules in qt folder
        would need this to actually import :py:mod:`tools`.
    """
    path = backintimePath(*path)

    if path not in sys.path:
        sys.path.insert(0, path)


def runningFromSource():
    """Check if BackInTime is running from source (without installing).

    Dev notes by buhtz (2024-04): This function is dangerous and will give a
    false-negative in fake filesystems (e.g. PyFakeFS). The function should
    not exist. Beside unit tests it is used only two times. Remove it until
    migration to pyproject.toml based project packaging (#1575).

    Returns:
        bool: ``True`` if BackInTime is running from source.
    """
    return os.path.isfile(backintimePath('common', 'backintime'))


def addSourceToPathEnviron():
    """
    Add 'backintime/common' path to 'PATH' environ variable.
    """
    source = backintimePath('common')
    path = os.getenv('PATH')
    if path and source not in path.split(':'):
        os.environ['PATH'] = '%s:%s' % (source, path)


def get_git_repository_info(path=None, hash_length=None):
    """Return the current branch and last commit hash.

    About the length of a commit hash. There is no strict rule but it is
    common sense that 8 to 10 characters are enough to be unique.

    Credits: https://stackoverflow.com/a/51224861/4865723

    Args:
        path (Path): Path with '.git' folder in (default is
                     current working directory).
        cut_hash (int): Restrict length of commit hash.

    Returns:
        (dict): Dict with keys "branch" and "hash" if it is a git repo,
                otherwise an `None`.
    """

    if not path:
        # Default is current working dir
        path = pathlib.Path.cwd()
    elif isinstance(path, str):
        # WORKAROUND until cmoplete migration to pathlib
        path = pathlib.Path(path)

    git_folder = path / '.git'

    if not git_folder.exists():
        return None

    result = {}

    # branch name
    with (git_folder / 'HEAD').open('r') as handle:
        val = handle.read()

    if not val.startswith('ref: '):
        result['branch'] = '(detached HEAD)'
        result['hash'] = val

    else:
        result['branch'] = '/'.join(val.split('/')[2:]).strip()

        # commit hash
        with (git_folder / 'refs' / 'heads' / result['branch']) \
            .open('r') as handle:
            result['hash'] = handle.read().strip()

    if hash_length:
        result['hash'] = result['hash'][:hash_length]

    return result


def readFile(path, default=None):
    """
    Read the file in ``path`` or its '.gz' compressed variant and return its
    content or ``default`` if ``path`` does not exist.

    Args:
        path (str):             full path to file that should be read.
                                '.gz' will be added automatically if the file
                                is compressed
        default (str):          default if ``path`` does not exist

    Returns:
        str:                    content of file in ``path``
    """
    ret_val = default

    try:
        if os.path.exists(path):

            with open(path) as f:
                ret_val = f.read()

        elif os.path.exists(path + '.gz'):

            with gzip.open(path + '.gz', 'rt') as f:
                ret_val = f.read()

    except:
        pass

    return ret_val


def readFileLines(path, default = None):
    """
    Read the file in ``path`` or its '.gz' compressed variant and return its
    content as a list of lines or ``default`` if ``path`` does not exist.

    Args:
        path (str):             full path to file that should be read.
                                '.gz' will be added automatically if the file
                                is compressed
        default (list):         default if ``path`` does not exist

    Returns:
        list:                   content of file in ``path`` split by lines.
    """
    ret_val = default

    try:
        if os.path.exists(path):
            with open(path) as f:
                ret_val = [x.rstrip('\n') for x in f.readlines()]
        elif os.path.exists(path + '.gz'):
            with gzip.open(path + '.gz', 'rt') as f:
                ret_val = [x.rstrip('\n') for x in f.readlines()]
    except:
        pass

    return ret_val


def older_than(dt: datetime, value: int, unit: TimeUnit) -> bool:
    """Return ``True`` if ``dt`` is older than ``value`` months, weeks, days or
    hours compared to the current time (`datetime.now()`).

    The resolution used is on microseconds level. Months are calculated based
    on calendar.

    Args:
        dt: Timestamp to be compared with on microsecond level.
        value: Number of units.
        unit: Specify to treat ``value`` as hours, days, weeks or months.

    Return:
        ``True`` if older, otherwise ``False``.
    """
    if not isinstance(unit, TimeUnit):
        unit = TimeUnit(unit)

    now = datetime.now()

    if unit is TimeUnit.HOUR:
        return dt < now - timedelta(hours=value)

    if unit is TimeUnit.DAY:
        return dt < now - timedelta(days=value)

    if unit is TimeUnit.WEEK:
        return dt < now - timedelta(weeks=value)

    if unit is TimeUnit.MONTH:
        # Calculate months based on calendar because timedelta do not support
        # months.
        compare_month = (dt.month + value - 1) % 12 + 1
        compare_year = dt.year + (dt.month + value - 1) // 12
        # make sure that day exist in the month
        last_day_dt \
            = datetime(compare_year, compare_month + 1, 1) - timedelta(days=1)
        compare_day = min(dt.day, last_day_dt.day)

        compare_dt = datetime(
            compare_year, compare_month, compare_day,
            now.hour, now.minute, now.microsecond)

        return now < compare_dt

    # Dev note (buhtz, 2024-09): This code branch already existed in the
    # original code (but silent, without throwing an exception). Even if it may
    # seem (nearly) pointless, it will be kept for now to ensure that it is
    # never executed.
    raise RuntimeError(f'Unexpected situation. {dt=} {value=} {unit=} '
                       'Please report it via a bug ticket.')


def checkCommand(cmd):
    """Check if command ``cmd`` is a file in 'PATH' environment.

    Args:
        cmd (str): The command.

    Returns:
        bool: ``True`` if ``cmd`` is in 'PATH' environment otherwise ``False``.
    """
    cmd = cmd.strip()

    if not cmd:
        return False

    if os.path.isfile(cmd):
        return True

    return which(cmd) is not None


def which(cmd):
    """Get the fullpath of executable command ``cmd``.

    Works like command-line 'which' command.

    Dev note by buhtz (2024-04): Give false-negative results in fake
    filesystems. Quit often use in the whole code base. But not sure why
    can we replace it with "which" from shell?

    Args:
        cmd (str): The command.

    Returns:
        str: Fullpath of command ``cmd`` or ``None`` if command is not
             available.
    """
    pathenv = os.getenv('PATH', '')
    path = pathenv.split(':')
    common = backintimePath('common')

    if runningFromSource() and common not in path:
        path.insert(0, common)

    for directory in path:
        fullpath = os.path.join(directory, cmd)

        if os.path.isfile(fullpath) and os.access(fullpath, os.X_OK):
            return fullpath

    return None


def makeDirs(path):
    """
    Create directories ``path`` recursive and return success.

    Args:
        path (str): fullpath to directories that should be created

    Returns:
        bool:       ``True`` if successful
    """
    path = path.rstrip(os.sep)
    if not path:
        return False

    if os.path.isdir(path):
        return True

    else:

        try:
            os.makedirs(path)
        except Exception as e:
            logger.error("Failed to make dirs '%s': %s"
                         % (path, str(e)), traceDepth=1)

    return os.path.isdir(path)


def mkdir(path, mode=0o755, enforce_permissions=True):
    """
    Create directory ``path``.

    Args:
        path (str): full path to directory that should be created
        mode (int): numeric permission mode

    Returns:
        bool:       ``True`` if successful
    """
    if os.path.isdir(path):
        try:
            if enforce_permissions:
                os.chmod(path, mode)
        except:
            return False

        return True

    else:
        os.mkdir(path, mode)

        if mode & 0o002 == 0o002:
            # make file world (other) writable was requested
            # debian and ubuntu won't set o+w with os.mkdir
            # this will fix it
            os.chmod(path, mode)

    return os.path.isdir(path)


def pids():
    """
    List all PIDs currently running on the system.

    Returns:
        list:   PIDs as int
    """
    return [int(x) for x in os.listdir('/proc') if x.isdigit()]


def processStat(pid):
    """
    Get the stat's of the process with ``pid``.

    Args:
        pid (int):  Process Indicator

    Returns:
        str:        stat from /proc/PID/stat
    """
    try:
        with open('/proc/{}/stat'.format(pid), 'rt') as f:
            return f.read()

    except OSError as e:
        logger.warning('Failed to read process stat from {}: [{}] {}'
                       .format(e.filename, e.errno, e.strerror))
        return ''


def processPaused(pid):
    """
    Check if process ``pid`` is paused (got signal SIGSTOP).

    Args:
        pid (int):  Process Indicator

    Returns:
        bool:       True if process is paused
    """
    m = re.match(r'\d+ \(.+\) T', processStat(pid))

    return bool(m)


def processName(pid):
    """
    Get the name of the process with ``pid``.

    Args:
        pid (int):  Process Indicator

    Returns:
        str:        name of the process
    """
    m = re.match(r'.*\((.+)\).*', processStat(pid))

    if m:
        return m.group(1)


def processCmdline(pid):
    """
    Get the cmdline (command that spawnd this process) of the process with
    ``pid``.

    Args:
        pid (int):  Process Indicator

    Returns:
        str:        cmdline of the process
    """
    try:
        with open('/proc/{}/cmdline'.format(pid), 'rt') as f:
            return f.read().strip('\n')
    except OSError as e:
        logger.warning('Failed to read process cmdline from {}: [{}] {}'.format(e.filename, e.errno, e.strerror))
        return ''

def pidsWithName(name):
    """
    Get all processes currently running with name ``name``.

    Args:
        name (str): name of a process like 'python3' or 'backintime'

    Returns:
        list:       PIDs as int
    """
    # /proc/###/stat stores just the first 16 chars of the process name
    return [x for x in pids() if processName(x) == name[:15]]

def processExists(name):
    """
    Check if process ``name`` is currently running.

    Args:
        name (str): name of a process like 'python3' or 'backintime'

    Returns:
        bool:       ``True`` if there is a process running with ``name``
    """
    return len(pidsWithName(name)) > 0

def processAlive(pid):
    """
    Check if the process with PID ``pid`` is alive.

    Args:
        pid (int):  Process Indicator

    Returns:
        bool:       ``True`` if the process with PID ``pid`` is alive

    Raises:
        ValueError: If ``pid`` is 0 because 'kill(0, SIG)' would send SIG to all
                    processes
    """
    if pid < 0:
        return False
    elif pid == 0:
        raise ValueError('invalid PID 0')
    else:
        try:
            os.kill(pid, 0) #this will raise an exception if the pid is not valid
        except OSError as err:
            if err.errno == errno.ESRCH:
                # ESRCH == No such process
                return False
            elif err.errno == errno.EPERM:
                # EPERM clearly means there's a process to deny access to
                return True
            else:
                raise
        else:
            return True

def checkXServer():
    """
    Check if there is a X11 server running on this system.

    Use ``is_Qt_working`` instead if you want to be sure that Qt is working.

    Returns:
        bool:   ``True`` if X11 server is running
    """
    # Note: Return values of xdpyinfo <> 0 are not clearly documented.
    #       xdpyinfo does indeed return 1 if it prints
    #           xdypinfo: unable to open display "..."
    #       This seems to be undocumented (at least not in the man pages)
    #       and the source is not obvious here:
    #       https://cgit.freedesktop.org/xorg/app/xdpyinfo/tree/xdpyinfo.c
    if checkCommand('xdpyinfo'):
        proc = subprocess.Popen(['xdpyinfo'],
                                stdout = subprocess.DEVNULL,
                                stderr = subprocess.DEVNULL)
        proc.communicate()
        return proc.returncode == 0
    else:
        return False


def is_Qt_working(systray_required=False):
    """
    Check if the Qt GUI library is working (installed and configured)

    This function is contained in BiT CLI (not BiT Qt) to allow Qt
    diagnostics output even if the BiT Qt GUI is not installed.
    This function does NOT add a hard Qt dependency (just "probing")
    so it is OK to be in BiT CLI.

    Args:
        systray_required: Set to ``True`` if the systray of the desktop
        environment must be available too to consider Qt as "working"

    Returns:
        bool: ``True``  Qt can create a GUI
              ``False`` Qt fails (or the systray is not available
                        if ``systray_required`` is ``True``)
    """

    # Spawns a new process since it may crash with a SIGABRT and we
    # don't want to crash BiT if this happens...

    try:
        path = os.path.join(backintimePath("common"), "qt_probing.py")
        cmd = [sys.executable, path]
        if logger.DEBUG:
            cmd.append('--debug')

        with subprocess.Popen(cmd,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              universal_newlines=True) as proc:

            std_output, error_output = proc.communicate(timeout=30)  # to get the exit code
            # "timeout" fixes #1592 (qt_probing.py may hang as root): Kill after timeout

            logger.debug(f"Qt probing result: exit code {proc.returncode}")

            if proc.returncode != 2 or logger.DEBUG:  # if some Qt parts are missing: Show details
                logger.debug(f"Qt probing stdout:\n{std_output}")
                logger.debug(f"Qt probing errout:\n{error_output}")

            return proc.returncode == 2 or (proc.returncode == 1 and systray_required is False)

    except FileNotFoundError:
        logger.error(f"Qt probing script not found: {cmd[0]}")
        raise

    # Fix for #1592 (qt_probing.py may hang as root): Kill after timeout
    except subprocess.TimeoutExpired:
        proc.kill()
        outs, errs = proc.communicate()
        logger.info("Qt probing sub process killed after timeout without response")
        logger.debug(f"Qt probing stdout:\n{outs}")
        logger.debug(f"Qt probing errout:\n{errs}")

    except Exception as e:
        logger.error(f"Error: {repr(e)}")
        raise


def preparePath(path):
    """
    Removes trailing slash '/' from ``path``.

    Args:
        path (str): absolute path

    Returns:
        str:        path ``path`` without trailing but with leading slash
    """
    path = path.strip("/")
    path = os.sep + path
    return path

def powerStatusAvailable():
    """
    Check if org.freedesktop.UPower is available so that
    :py:func:`tools.onBattery` would return the correct power status.

    Returns:
        bool:   ``True`` if :py:func:`tools.onBattery` can report power status
    """
    if dbus:
        try:
            bus = dbus.SystemBus()
            proxy = bus.get_object('org.freedesktop.UPower',
                                   '/org/freedesktop/UPower')
            return 'OnBattery' in proxy.GetAll('org.freedesktop.UPower',
                            dbus_interface = 'org.freedesktop.DBus.Properties')
        except dbus.exceptions.DBusException:
            pass
    return False

def onBattery():
    """
    Checks if the system is on battery power.

    Returns:
        bool:   ``True`` if system is running on battery
    """
    if dbus:
        try:
            bus = dbus.SystemBus()
            proxy = bus.get_object('org.freedesktop.UPower',
                                   '/org/freedesktop/UPower')
            return bool(proxy.Get('org.freedesktop.UPower',
                                  'OnBattery',
                                  dbus_interface = 'org.freedesktop.DBus.Properties'))
        except dbus.exceptions.DBusException:
            pass
    return False

def rsyncCaps(data = None):
    """
    Get capabilities of the installed rsync binary. This can be different from
    version to version and also on build arguments used when building rsync.

    Args:
        data (str): 'rsync --version' output. This is just for unittests.

    Returns:
        list:       List of str with rsyncs capabilities
    """
    if not data:
        proc = subprocess.Popen(['rsync', '--version'],
                                stdout = subprocess.PIPE,
                                universal_newlines = True)
        data = proc.communicate()[0]
    caps = []
    #rsync >= 3.1 does provide --info=progress2
    matchers = [r'rsync\s*version\s*(\d\.\d)', r'rsync\s*version\s*v(\d\.\d.\d)']
    for matcher in matchers:
        m = re.match(matcher, data)
        if m and Version(m.group(1)) >= Version('3.1'):
            caps.append('progress2')
            break

    #all other capabilities are separated by ',' between
    #'Capabilities:' and '\n\n'
    m = re.match(r'.*Capabilities:(.+)\n\n.*', data, re.DOTALL)
    if not m:
        return caps

    for line in m.group(1).split('\n'):
        caps.extend([i.strip(' \n') for i in line.split(',') if i.strip(' \n')])
    return caps


def rsyncPrefix(config,
                no_perms=True,
                use_mode=['ssh', 'ssh_encfs'],
                progress=True):
    """
    Get rsync command and all args for creating a new snapshot. Args are
    based on current profile in ``config``.

    Args:
        config (config.Config): current config
        no_perms (bool):        don't sync permissions (--no-p --no-g --no-o)
                                if ``True``.
                                :py:func:`config.Config.preserveAcl` == ``True`` or
                                :py:func:`config.Config.preserveXattr` == ``True``
                                will overwrite this to ``False``
        use_mode (list):        if current mode is in this list add additional
                                args for that mode
        progress (bool):        add '--info=progress2' to show progress

    Returns:
        list:                   rsync command with all args but without
                                --include, --exclude, source and destination
    """
    caps = rsyncCaps()
    cmd = []

    if config.nocacheOnLocal():
        cmd.append('nocache')

    cmd.append('rsync')

    cmd.extend((
        # recurse into directories
        '--recursive',
        # preserve modification times
        '--times',
        # preserve device files (super-user only)
        '--devices',
        # preserve special files
        '--specials',
        # preserve hard links
        '--hard-links',
        # numbers in a human-readable format
        '--human-readable',
        # use "new" argument protection
        '-s'
    ))

    if config.useChecksum() or config.forceUseChecksum:
        cmd.append('--checksum')

    if config.copyUnsafeLinks():
        cmd.append('--copy-unsafe-links')

    if config.copyLinks():
        cmd.append('--copy-links')
    else:
        cmd.append('--links')

    if config.oneFileSystem():
        cmd.append('--one-file-system')

    if config.preserveAcl() and "ACLs" in caps:
        cmd.append('--acls')  # preserve ACLs (implies --perms)
        no_perms = False

    if config.preserveXattr() and "xattrs" in caps:
        cmd.append('--xattrs')  # preserve extended attributes
        no_perms = False

    if no_perms:
        cmd.extend(('--no-perms', '--no-group', '--no-owner'))
    else:
        cmd.extend(('--perms',          # preserve permissions
                    '--executability',  # preserve executability
                    '--group',         # preserve group
                    '--owner'))         # preserve owner (super-user only)

    if progress and 'progress2' in caps:
        cmd.extend(('--info=progress2',
                    '--no-inc-recursive'))

    if config.bwlimitEnabled():
        cmd.append('--bwlimit=%d' % config.bwlimit())

    if config.rsyncOptionsEnabled():
        cmd.extend(shlex.split(config.rsyncOptions()))

    cmd.extend(rsyncSshArgs(config, use_mode))
    return cmd


def rsyncSshArgs(config, use_mode=['ssh', 'ssh_encfs']):
    """
    Get SSH args for rsync based on current profile in ``config``.

    Args:
        config (config.Config): Current config instance.
        use_mode (list):        If the profiles current mode is in this list
                                add additional args.

    Returns:
        list:                   List of rsync args related to SSH.
    """

    cmd = []

    mode = config.snapshotsMode()

    if mode in ['ssh', 'ssh_encfs'] and mode in use_mode:
        ssh = config.sshCommand(user_host=False,
                                ionice=False,
                                nice=False)

        cmd.append('--rsh=' + ' '.join(ssh))

        if config.niceOnRemote() \
           or config.ioniceOnRemote() \
           or config.nocacheOnRemote():

            rsync_path = '--rsync-path='

            if config.niceOnRemote():
                rsync_path += 'nice -n 19 '

            if config.ioniceOnRemote():
                rsync_path += 'ionice -c2 -n7 '

            if config.nocacheOnRemote():
                rsync_path += 'nocache '

            rsync_path += 'rsync'

            cmd.append(rsync_path)

    return cmd


def rsyncRemove(config, run_local = True):
    """
    Get rsync command and all args for removing snapshots with rsync.

    Args:
        config (config.Config): current config
        run_local (bool):       if True and current mode is ``ssh``
                                or ``ssh_encfs`` this will add SSH options

    Returns:
        list:                   rsync command with all args
    """
    cmd = ['rsync', '-a', '--delete', '-s']
    if run_local:
        cmd.extend(rsyncSshArgs(config))
    return cmd

#TODO: check if we really need this
def tempFailureRetry(func, *args, **kwargs):
    while True:
        try:
            return func(*args, **kwargs)
        except (os.error, IOError) as ex:
            if ex.errno == errno.EINTR:
                continue
            else:
                raise

def md5sum(path):
    """
    Calculate md5sum for file in ``path``.

    Args:
        path (str): full path to file

    Returns:
        str:        md5sum of file
    """
    md5 = hashlib.md5()
    with open(path, 'rb') as f:
        while True:
            data = f.read(4096)
            if not data:
                break
            md5.update(data)
    return md5.hexdigest()

def checkCronPattern(s):
    """
    Check if ``s`` is a valid cron pattern.
    Examples::

        0,10,13,15,17,20,23
        */6

    Args:
        s (str):    pattern to check

    Returns:
        bool:       ``True`` if ``s`` is a valid cron pattern

    Dev note: Schedule for removal. See comment in
    `config.Config.saveProfile()`.
    """
    if s.find(' ') >= 0:
        return False
    try:
        if s.startswith('*/'):
            if s[2:].isdigit() and int(s[2:]) <= 24:
                return True
            else:
                return False
        for i in s.split(','):
            if i.isdigit() and int(i) <= 24:
                continue
            else:
                return False
        return True
    except ValueError:
        return False


#TODO: check if this is still necessary
def checkHomeEncrypt():
    """
    Return ``True`` if users home is encrypted
    """
    home = os.path.expanduser('~')
    if not os.path.ismount(home):
        return False
    if checkCommand('ecryptfs-verify'):
        try:
            subprocess.check_call(['ecryptfs-verify', '--home'],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            pass
        else:
            return True
    if checkCommand('encfs'):
        proc = subprocess.Popen(['mount'], stdout=subprocess.PIPE, universal_newlines = True)
        mount = proc.communicate()[0]
        r = re.compile('^encfs on %s type fuse' % home)
        for line in mount.split('\n'):
            if r.match(line):
                return True
    return False


def envLoad(f):
    """
    Load environ variables from file ``f`` into current environ.
    Do not overwrite existing environ variables.

    Args:
        f (str):    full path to file with environ variables
    """
    env = os.environ.copy()
    env_file = configfile.ConfigFile()
    env_file.load(f, maxsplit = 1)
    for key in env_file.keys():
        value = env_file.strValue(key)
        if not value:
            continue
        if not key in list(env.keys()):
            os.environ[key] = value
    del env_file


def envSave(f):
    """
    Save environ variables to file that are needed by cron
    to connect to keyring. This will only work if the user is logged in.

    Args:
        f (str):    full path to file for environ variables
    """
    env = os.environ.copy()
    env_file = configfile.ConfigFile()
    for key in ('GNOME_KEYRING_CONTROL', 'DBUS_SESSION_BUS_ADDRESS', \
                'DBUS_SESSION_BUS_PID', 'DBUS_SESSION_BUS_WINDOWID', \
                'DISPLAY', 'XAUTHORITY', 'GNOME_DESKTOP_SESSION_ID', \
                'KDE_FULL_SESSION'):
        if key in env:
            env_file.setStrValue(key, env[key])

    env_file.save(f)


def keyringSupported():
    """
    Checks if a keyring (supported by BiT) is available

    Returns:
         bool: ``True`` if a supported keyring could be loaded
    """

    if not is_keyring_available:
        logger.debug('No keyring due to import error.')
        return False

    keyring_config_file_folder = "Unknown"
    try:
        keyring_config_file_folder = keyring.util.platform_.config_root()
    except:
        pass

    logger.debug(f"Keyring config file folder: {keyring_config_file_folder}")

    # Determine the currently active backend
    try:
        # get_keyring() internally calls keyring.core.init_backend()
        # which fixes non-available backends for the first call.
        # See related issue #1321:
        # https://github.com/bit-team/backintime/issues/1321
        # The module name is used instead of the class name
        # to show only the keyring name (not the technical name)
        displayName = keyring.get_keyring().__module__
    except:
        displayName = str(keyring.get_keyring())  # technical class name!

    logger.debug("Available keyring backends:")

    try:
        for b in backend.get_all_keyring():
            logger.debug(b)
    except Exception as e:
        logger.debug("Available backends cannot be listed: " + repr(e))

    available_backends = []

    # Create a list of installed backends that BiT supports (white-listed).
    # This is done by trying to put the meta classes ("class definitions",
    # NOT instances of the class itself!) of the supported backends
    # into the "backends" list

    backends_to_check = [
        (keyring.backends, ['SecretService', 'Keyring']),
        (keyring.backends, ['Gnome', 'Keyring']),
        (keyring.backends, ['kwallet', 'Keyring']),
        (keyring.backends, ['kwallet', 'DBusKeyring']),
        (keyring.backend, ['SecretServiceKeyring']),
        (keyring.backend, ['GnomeKeyring']),
        (keyring.backend, ['KDEWallet']),
        # See issue #1410: ChainerBackend is now supported to solve the
        # problem of configuring the used backend since it iterates over all
        # of them and is to be the default backend now. Please read the issue
        # details to understand the unwanted side-effects the chainer could
        # bring with it.
        # See also:
        # https://github.com/jaraco/keyring/blob/977ed03677bb0602b91f005461ef3dddf01a49f6/keyring/backends/chainer.py#L11  # noqa
        (keyring.backends, ('chainer', 'ChainerBackend')),
    ]

    not_found_metaclasses = []

    for backend_package, backends in backends_to_check:
        result = backend_package  # e.g. keyring.backends


        try:
            # Load the backend step-by-step.
            # e.g. When the target is "keyring.backends.Gnome.Keyring" then in
            # a first step "Gnome" part is loaded first and if successful the
            # "keyring" part.
            for b in backends:
                result = getattr(result, b)

        except AttributeError as err:
            # # Debug message if backend is not available.
            # logger.debug('Metaclass {}.{} not found: {}'
            #              .format(backend_package.__name__,
            #                      '.'.join(backends),
            #                      repr(err)))
            not_found_metaclasses.append('{}.{}'.format(
                backend_package.__name__, '.'.join(backends)))

        else:
            # Remember the backend class (not an instance) as available.
            available_backends.append(result)

    logger.debug(f'Not found Metaclasses: {not_found_metaclasses}')
    logger.debug("Available supported backends: " + repr(available_backends))

    if available_backends and isinstance(keyring.get_keyring(), tuple(available_backends)):
        logger.debug("Found appropriate keyring '{}'".format(displayName))
        return True

    logger.debug(f"No appropriate keyring found. '{displayName}' can't be "
                 "used with BackInTime.")
    logger.debug("See https://github.com/bit-team/backintime on how to fix "
                 "this by creating a keyring config file.")

    return False


def password(*args):

    if is_keyring_available:
        return keyring.get_password(*args)
    return None


def setPassword(*args):

    if is_keyring_available:
        return keyring.set_password(*args)
    return False


def mountpoint(path):
    """
    Get the mountpoint of ``path``. If your HOME is on a separate partition
    mountpoint('/home/user/foo') would return '/home'.

    Args:
        path (str): full path

    Returns:
        str:        mountpoint of the filesystem
    """
    path = os.path.realpath(os.path.abspath(path))

    while path != os.path.sep:
        if os.path.ismount(path):
            return path

        path = os.path.abspath(os.path.join(path, os.pardir))

    return path


def decodeOctalEscape(s):
    """
    Decode octal-escaped characters with its ASCII dependence.
    For example '\040' will be a space ' '

    Args:
        s (str):    string with or without octal-escaped characters

    Returns:
        str:        human readable string
    """
    def repl(m):
        return chr(int(m.group(1), 8))
    return re.sub(r'\\(\d{3})', repl, s)


def mountArgs(path):
    """
    Get all /etc/mtab args for the filesystem of ``path`` as a list.
    Example::

        [DEVICE,      MOUNTPOINT, FILESYSTEM_TYPE, OPTIONS,    DUMP, PASS]
        ['/dev/sda3', '/',        'ext4',          'defaults', '0',  '0']
        ['/dev/sda1', '/boot',    'ext4',          'defaults', '0',  '0']

    Args:
        path (str): full path

    Returns:
        list:       mount args
    """
    mp = mountpoint(path)

    with open('/etc/mtab', 'r') as mounts:

        for line in mounts:
            args = line.strip('\n').split(' ')

            if len(args) >= 2:
                args[1] = decodeOctalEscape(args[1])

                if args[1] == mp:
                    return args

    return None


def device(path):
    """
    Get the device for the filesystem of ``path``.
    Example::

        /dev/sda1
        /dev/mapper/vglinux
        proc

    Args:
        path (str): full path

    Returns:
        str:        device
    """
    args = mountArgs(path)

    if args:
        return args[0]

    return None


def filesystem(path):
    """
    Get the filesystem type for the filesystem of ``path``.

    Args:
        path (str): full path

    Returns:
        str:        filesystem
    """
    args = mountArgs(path)
    if args and len(args) >= 3:
        return args[2]
    return None

def _uuidFromDev_via_filesystem(dev):
    """Get the UUID for the block device ``dev`` from ``/dev/disk/by-uuid`` in
    the filesystem.

    Args:
        dev (pathlib.Path): The block device path (e.g. ``/dev/sda1``).

    Returns:
        str: The UUID or ``None`` if nothing found.
    """


    # /dev/disk/by-uuid
    path_DISK_BY_UUID = pathlib.Path(DISK_BY_UUID)

    if not path_DISK_BY_UUID.exists():
        return None

    # Each known uuid
    for uuid_symlink in path_DISK_BY_UUID.glob('*'):

        # Resolve the symlink (get it's target) to get the real device name
        # and compare it with the device we are looking for
        if dev == uuid_symlink.resolve():

            # e.g. 'c7aca0a7-89ed-43f0-a4f9-c744dfe673e0'
            return uuid_symlink.name

    # Nothing found
    return None

def _uuidFromDev_via_blkid_command(dev):
    """Get the UUID for the block device ``dev`` via the extern command
    ``blkid``.

    Hint:
        On most systems the ``blkid`` command is available only for the
        super-user (e.g. via ``sudo``).

    Args:
        dev (pathlib.Path): The block device path (e.g. ``/dev/sda1``).

    Returns:
        str: The UUID or ``None`` if nothing found.
    """

    # Call "blkid" command
    try:
        # If device does not exist, blkid will exit with a non-zero code
        output = subprocess.check_output(['blkid', dev],
                                        stderr = subprocess.DEVNULL,
                                        universal_newlines=True)

    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    # Parse the commands output for a UUID
    try:
        return re.findall(r'.*\sUUID=\"([^\"]*)\".*', output)[0]
    except IndexError:
        # nothing found via the regex pattern
        pass

    return None

def _uuidFromDev_via_udevadm_command(dev):
    """Get the UUID for the block device ``dev`` via the extern command
    ``udevadm``.

    Args:
        dev (pathlib.Path): The block device path (e.g. ``/dev/sda1``).

    Returns:
        str: The UUID or ``None`` if nothing found.
    """
    # Call "udevadm" command
    try:
        output = subprocess.check_output(['udevadm', 'info', f'--name={dev}'],
                                        stderr = subprocess.DEVNULL,
                                        universal_newlines=True)

    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    # Parse the commands output for a UUID
    try:
        return re.findall(r'.*?ID_FS_UUID=(\S+)', output)[0]
    except IndexError:
        # nothing found via the regex pattern
        pass

    return None


def uuidFromDev(dev):
    """
    Get the UUID for the block device ``dev``.

    Args:
        dev (str, pathlib.Path):  block device path

    Returns:
        str:        UUID
    """

    # handle Path objects only
    if not isinstance(dev, pathlib.Path):
        dev = pathlib.Path(dev)

    if dev.exists():
        dev = dev.resolve()  # when /dev/sda1 is a symlink

        # Look at /dev/disk/by-uuid/
        uuid = _uuidFromDev_via_filesystem(dev)
        if uuid:
            return uuid

        # Try extern command "blkid"
        uuid = _uuidFromDev_via_blkid_command(dev)
        if uuid:
            return uuid

    # "dev" doesn't exist in the filesystem

    # Try "udevadm" command at the end
    return _uuidFromDev_via_udevadm_command(dev)


def uuidFromPath(path):
    """
    Get the UUID for the for the filesystem of ``path``.

    Args:
        path (str): full path

    Returns:
        str:        UUID
    """
    return uuidFromDev(device(path))


def isRoot():
    """
    Check if we are root.

    Returns:
        bool:   ``True`` if we are root
    """

    # The EUID (Effective UID) may be different from the UID (user ID)
    # in case of SetUID or using "sudo" (where EUID is "root" and UID
    # is the original user who executed "sudo").
    return os.geteuid() == 0

def usingSudo():
    """
    Check if 'sudo' was used to start this process.

    Returns:
        bool:   ``True`` if the process was started with sudo
    """
    return isRoot() and os.getenv('HOME', '/root') != '/root'

re_wildcard = re.compile(r'(?:\[|\]|\?)')
re_asterisk = re.compile(r'\*')
re_separate_asterisk = re.compile(r'(?:^\*+[^/\*]|[^/\*]\*+[^/\*]|[^/\*]\*+|\*+[^/\*]|[^/\*]\*+$)')

def patternHasNotEncryptableWildcard(pattern):
    """
    Check if ``pattern`` has wildcards ``[ ] ? *``.
    but return ``False`` for ``foo/*``, ``foo/*/bar``, ``*/bar`` or ``**/bar``

    Args:
        pattern (str):  path or pattern to check

    Returns:
        bool:           ``True`` if ``pattern`` has wildcards ``[ ] ? *`` but
                        ``False`` if wildcard look like
                        ``foo/*``, ``foo/*/bar``, ``*/bar`` or ``**/bar``
    """
    if not re_wildcard.search(pattern) is None:
        return True

    if not re_asterisk is None and not re_separate_asterisk.search(pattern) is None:
        return True
    return False


def readTimeStamp(fname):
    """
    Read date string from file ``fname`` and try to return datetime.

    Args:
        fname (str): Full path to timestamp file.

    Returns:
        datetime.datetime: Timestamp object.
    """

    if not os.path.exists(fname):
        logger.debug(f"No timestamp file '{fname}'")
        return

    with open(fname, 'r') as f:
        s = f.read().strip('\n')

    time_formats = (
        '%Y%m%d %H%M',  # BIT like
        '%Y%m%d',  # Anacron like
    )

    for form in time_formats:

        try:
            stamp = datetime.strptime(s, form)

        except ValueError:
            # invalid format
            # next iteration
            pass

        else:
            # valid time stamp
            logger.debug(f"Read timestamp '{stamp}' from file '{fname}'")

            return stamp


def writeTimeStamp(fname):
    """Write current date and time into file ``fname``.

    Args:
        fname (str): Full path to timestamp file.
    """
    now = datetime.now().strftime('%Y%m%d %H%M')
    logger.debug(f"Write timestamp '{now}' into file '{fname}'")
    makeDirs(os.path.dirname(fname))

    with open(fname, 'w') as f:
        f.write(now)


INHIBIT_LOGGING_OUT = 1
INHIBIT_USER_SWITCHING = 2
INHIBIT_SUSPENDING = 4
INHIBIT_IDLE = 8

INHIBIT_DBUS = (
               {'service':      'org.gnome.SessionManager',
                'objectPath':   '/org/gnome/SessionManager',
                'methodSet':    'Inhibit',
                'methodUnSet':  'Uninhibit',
                'interface':    'org.gnome.SessionManager',
                'arguments':    (0, 1, 2, 3)
               },
               {'service':      'org.mate.SessionManager',
                'objectPath':   '/org/mate/SessionManager',
                'methodSet':    'Inhibit',
                'methodUnSet':  'Uninhibit',
                'interface':    'org.mate.SessionManager',
                'arguments':    (0, 1, 2, 3)
               },
               {'service':      'org.freedesktop.PowerManagement',
                'objectPath':   '/org/freedesktop/PowerManagement/Inhibit',
                'methodSet':    'Inhibit',
                'methodUnSet':  'UnInhibit',
                'interface':    'org.freedesktop.PowerManagement.Inhibit',
                'arguments':    (0, 2)
               })

def inhibitSuspend(app_id = sys.argv[0],
                    toplevel_xid = None,
                    reason = 'take snapshot',
                    flags = INHIBIT_SUSPENDING | INHIBIT_IDLE):
    """
    Prevent machine to go to suspend or hibernate.
    Returns the inhibit cookie which is used to end the inhibitor.
    """
    if ON_TRAVIS or dbus is None:
        # no suspend on travis (no dbus either)
        return

    # Fixes #1592 (BiT hangs as root when trying to establish a dbus user session connection)
    # Side effect: In BiT <= 1.4.1 root still tried to connect to the dbus user session
    #              and it may have worked sometimes (without logging we don't know)
    #              so as root suspend can no longer inhibited.
    if isRoot():
        logger.debug("Inhibit Suspend failed because BIT was started as root.")
        return

    if not app_id:
        app_id = 'backintime'
    try:
        if not toplevel_xid:
            toplevel_xid = 0
    except IndexError:
        toplevel_xid = 0

    for dbus_props in INHIBIT_DBUS:
        try:
            #connect directly to the socket instead of dbus.SessionBus because
            #the dbus.SessionBus was initiated before we loaded the environ
            #variables and might not work
            if 'DBUS_SESSION_BUS_ADDRESS' in os.environ:
                bus = dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])
            else:
                bus = dbus.SessionBus()  # This code may hang forever (if BiT is run as root via cron job and no user is logged in). See #1592
            interface = bus.get_object(dbus_props['service'], dbus_props['objectPath'])
            proxy = interface.get_dbus_method(dbus_props['methodSet'], dbus_props['interface'])
            cookie = proxy(*[(app_id, dbus.UInt32(toplevel_xid), reason, dbus.UInt32(flags))[i] for i in dbus_props['arguments']])
            logger.debug('Inhibit Suspend started. Reason: {}'.format(reason))
            return (cookie, bus, dbus_props)
        except dbus.exceptions.DBusException:
            pass
    logger.warning('Inhibit Suspend failed.')

def unInhibitSuspend(cookie, bus, dbus_props):
    """
    Release inhibit.
    """
    assert isinstance(cookie, int), 'cookie is not int type: %s' % cookie
    assert isinstance(bus, dbus.bus.BusConnection), 'bus is not dbus.bus.BusConnection type: %s' % bus
    assert isinstance(dbus_props, dict), 'dbus_props is not dict type: %s' % dbus_props
    try:
        interface = bus.get_object(dbus_props['service'], dbus_props['objectPath'])
        proxy = interface.get_dbus_method(dbus_props['methodUnSet'], dbus_props['interface'])
        proxy(cookie)
        logger.debug('Release inhibit Suspend')
        return None
    except dbus.exceptions.DBusException:
        logger.warning('Release inhibit Suspend failed.')
        return (cookie, bus, dbus_props)


def splitCommands(cmds, head = '', tail = '', maxLength = 0):
    """
    Split a list of commands ``cmds`` into multiple commands with each length
    lower than ``maxLength``.

    Args:
        cmds (list):            commands
        head (str):             command that need to run first on every
                                iteration of ``cmds``
        tail (str):             command that need to run after every iteration
                                of ``cmds``
        maxLength (int):        maximum length a command could be.
                                Don't split if <= 0

    Yields:
        str:                    new command with length < ``maxLength``

    Example::

        head cmds[0] cmds[n] tail
    """
    while cmds:
        s = head
        while cmds and ((len(s + cmds[0] + tail) <= maxLength) or maxLength <= 0):
            s += cmds.pop(0)
        s += tail
        yield s


def escapeIPv6Address(address):
    """Escape IP addresses with square brackets ``[]`` if they are IPv6.

    If it is an IPv4 address or a hostname (lettersonly) nothing is changed.

    Args:
        address (str): IP-Address to escape if needed.

    Returns:
        str: The address, escaped if it is IPv6.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        # invalid IP, e.g. a hostname
        return address

    if ip.version == 6:
        return f'[{address}]'

    return address


def camelCase(s):
    """
    Remove underlines and make every first char uppercase.

    Args:
        s (str):    string separated by underlines (foo_bar)

    Returns:
        str:        string without underlines but uppercase chars (FooBar)
    """
    return ''.join([x.capitalize() for x in s.split('_')])


class Alarm:
    """Establish a callback function that is called after a timeout using
    SIGALRM signal.

    If no callback is specified a `exception.Timeout` will be raised instead.
    The implementation uses a SIGALRM signal. Attention: Do not call code in
    the callback that does not support multi-threading (reentrance) or you may
    cause non-deterministic "random" RuntimeErrors (RTE).
    """

    def __init__(self, callback=None, overwrite=True):
        """Create a new alarm instance.

        Args:
            callback (callable): Function to call when the timer ran down
                (ensure calling only reentrant code). Use ``None`` to throw a
                `exceptions.Timeout` exception instead.
            overwrite (bool): Is it allowed to (re)start the timer even though
                the current timer is still running ("ticking"). ``True``
                cancels the current timer (if active) and restarts with the new
                timeout. ``False`` silently ignores the start request if the
                current timer is still "ticking"
        """
        self.callback = callback
        self.ticking = False
        self.overwrite = overwrite

    def start(self, timeout):
        """Start the timer (which calls the handler function
        when the timer ran down).

        If `self.overwrite` is ``False`` and the current timer is still ticking
        the start is silently ignored.

        Args:
            timeout: Timer count down in seconds.
        """
        if self.ticking and not self.overwrite:
            return

        try:
            # Warning: This code may cause non-deterministic RunTimeError
            #          if the handler function calls code that does
            #          not support reentrance (see e.g. issue #1003).
            signal.signal(signal.SIGALRM, self.handler)
            signal.alarm(timeout)
        except ValueError:
            # Why???
            pass

        self.ticking = True

    def stop(self):
        """Stop timer before it comes to an end."""
        try:
            signal.alarm(0)
            self.ticking = False

        # TODO: What to catch?
        except:
            pass

    def handler(self, signum, frame):
        """This method is called after the timer ran down to zero
        and calls the callback function of the alarm instance.

        Raises:
            `exceptions.Timeout`: If no callback function was set for the alarm
                instance.
        """
        self.ticking = False

        if self.callback is None:
            raise Timeout()

        else:
            self.callback()


class ShutDown:
    """
    Shutdown the system after the current snapshot has finished.
    This should work for KDE, Gnome, Unity, Cinnamon, XFCE, Mate and E17.
    """
    DBUS_SHUTDOWN ={'gnome':   {'bus':          'sessionbus',
                                'service':      'org.gnome.SessionManager',
                                'objectPath':   '/org/gnome/SessionManager',
                                'method':       'Shutdown',
                                    #methods    Shutdown
                                    #           Reboot
                                    #           Logout
                                'interface':    'org.gnome.SessionManager',
                                'arguments':    ()
                                    #arg (only with Logout)
                                    #           0 normal
                                    #           1 no confirm
                                    #           2 force
                               },
                    'kde':     {'bus':          'sessionbus',
                                'service':      'org.kde.ksmserver',
                                'objectPath':   '/KSMServer',
                                'method':       'logout',
                                'interface':    'org.kde.KSMServerInterface',
                                'arguments':    (-1, 2, -1)
                                    #1st arg   -1 confirm
                                    #           0 no confirm
                                    #2nd arg   -1 full dialog with default logout
                                    #           0 logout
                                    #           1 restart
                                    #           2 shutdown
                                    #3rd arg   -1 wait 30sec
                                    #           2 immediately
                               },
                    'xfce':    {'bus':          'sessionbus',
                                'service':      'org.xfce.SessionManager',
                                'objectPath':   '/org/xfce/SessionManager',
                                'method':       'Shutdown',
                                    #methods    Shutdown
                                    #           Restart
                                    #           Suspend (no args)
                                    #           Hibernate (no args)
                                    #           Logout (two args)
                                'interface':    'org.xfce.Session.Manager',
                                'arguments':    (True,)
                                    #arg        True    allow saving
                                    #           False   don't allow saving
                                    #1st arg (only with Logout)
                                    #           True    show dialog
                                    #           False   don't show dialog
                                    #2nd arg (only with Logout)
                                    #           True    allow saving
                                    #           False   don't allow saving
                               },
                    'mate':    {'bus':          'sessionbus',
                                'service':      'org.mate.SessionManager',
                                'objectPath':   '/org/mate/SessionManager',
                                'method':       'Shutdown',
                                    #methods    Shutdown
                                    #           Logout
                                'interface':    'org.mate.SessionManager',
                                'arguments':    ()
                                    #arg (only with Logout)
                                    #           0 normal
                                    #           1 no confirm
                                    #           2 force
                               },
                    'e17':     {'bus':          'sessionbus',
                                'service':      'org.enlightenment.Remote.service',
                                'objectPath':   '/org/enlightenment/Remote/RemoteObject',
                                'method':       'Halt',
                                    #methods    Halt -> Shutdown
                                    #           Reboot
                                    #           Logout
                                    #           Suspend
                                    #           Hibernate
                                'interface':    'org.enlightenment.Remote.Core',
                                'arguments':    ()
                               },
                    'e19':     {'bus':          'sessionbus',
                                'service':      'org.enlightenment.wm.service',
                                'objectPath':   '/org/enlightenment/wm/RemoteObject',
                                'method':       'Shutdown',
                                    #methods    Shutdown
                                    #           Restart
                                'interface':    'org.enlightenment.wm.Core',
                                'arguments':    ()
                               },
                    'z_freed': {'bus':          'systembus',
                                'service':      'org.freedesktop.login1',
                                'objectPath':   '/org/freedesktop/login1',
                                'method':       'PowerOff',
                                'interface':    'org.freedesktop.login1.Manager',
                                'arguments':    (True,)
                               }
                   }

    def __init__(self):
        self.is_root = isRoot()
        if self.is_root:
            self.proxy, self.args = None, None
        else:
            self.proxy, self.args = self._prepair()
        self.activate_shutdown = False
        self.started = False

    def _prepair(self):
        """
        Try to connect to the given dbus services. If successful it will
        return a callable dbus proxy and those arguments.
        """
        try:
            if 'DBUS_SESSION_BUS_ADDRESS' in os.environ:
                sessionbus = dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])
            else:
                sessionbus = dbus.SessionBus()
            systembus  = dbus.SystemBus()
        except:
            return (None, None)
        des = list(self.DBUS_SHUTDOWN.keys())
        des.sort()
        for de in des:
            if de == 'gnome' and self.unity7():
                continue
            dbus_props = self.DBUS_SHUTDOWN[de]
            try:
                if dbus_props['bus'] == 'sessionbus':
                    bus = sessionbus
                else:
                    bus = systembus
                interface = bus.get_object(dbus_props['service'], dbus_props['objectPath'])
                proxy = interface.get_dbus_method(dbus_props['method'], dbus_props['interface'])
                return (proxy, dbus_props['arguments'])
            except dbus.exceptions.DBusException:
                continue
        return (None, None)

    def canShutdown(self):
        """
        Indicate if a valid dbus service is available to shutdown system.
        """
        return not self.proxy is None or self.is_root

    def askBeforeQuit(self):
        """
        Indicate if ShutDown is ready to fire and so the application
        shouldn't be closed.
        """
        return self.activate_shutdown and not self.started

    def shutdown(self):
        """
        Run 'shutdown -h now' if we are root or
        call the dbus proxy to start the shutdown.
        """
        if not self.activate_shutdown:
            return False

        if self.is_root:
            self.started = True
            proc = subprocess.Popen(['shutdown', '-h', 'now'])
            proc.communicate()
            return proc.returncode

        if self.proxy is None:
            return False

        else:
            self.started = True

            return self.proxy(*self.args)

    def unity7(self):
        """
        Unity >= 7.0 doesn't shutdown automatically. It will
        only show shutdown dialog and wait for user input.
        """
        if not checkCommand('unity'):
            return False
        proc = subprocess.Popen(['unity', '--version'],
                                stdout = subprocess.PIPE,
                                universal_newlines = True)
        unity_version = proc.communicate()[0]
        m = re.match(r'unity ([\d\.]+)', unity_version)

        return m and Version(m.group(1)) >= Version('7.0') and processExists('unity-panel-service')


class SetupUdev:
    """
    Setup Udev rules for starting BackInTime when a drive get connected.
    This is done by serviceHelper.py script (included in backintime-qt)
    running as root though DBus.
    """
    CONNECTION = 'net.launchpad.backintime.serviceHelper'
    OBJECT = '/UdevRules'
    INTERFACE = 'net.launchpad.backintime.serviceHelper.UdevRules'
    MEMBERS = ('addRule', 'save', 'delete')

    def __init__(self):
        if dbus is None:
            self.isReady = False

            return

        try:
            bus = dbus.SystemBus()
            conn = bus.get_object(SetupUdev.CONNECTION, SetupUdev.OBJECT)
            self.iface = dbus.Interface(conn, SetupUdev.INTERFACE)

        except dbus.exceptions.DBusException as e:
            # Only DBusExceptions are  handled to do a "graceful recovery"
            # by working without a serviceHelper D-Bus connection...
            # All other exceptions are still raised causing BiT
            # to stop during startup.
            # if e._dbus_error_name in ('org.freedesktop.DBus.Error.NameHasNoOwner',
            #                           'org.freedesktop.DBus.Error.ServiceUnknown',
            #                           'org.freedesktop.DBus.Error.FileNotFound'):
            logger.warning('Failed to connect to Udev serviceHelper daemon '
                           'via D-Bus: ' + e.get_dbus_name())
            logger.warning('D-Bus message: ' + e.get_dbus_message())
            logger.warning('Udev-based profiles cannot be changed or checked '
                           'due to Udev serviceHelper connection failure')
            conn = None

            # else:
            #     raise

        self.isReady = bool(conn)

    def addRule(self, cmd, uuid):
        """Prepare rules in serviceHelper.py
        """
        if not self.isReady:
            return

        try:
            return self.iface.addRule(cmd, uuid)

        except dbus.exceptions.DBusException as exc:
            if exc._dbus_error_name == 'net.launchpad.backintime.InvalidChar':
                raise InvalidChar(str(exc)) from exc

            elif exc._dbus_error_name == 'net.launchpad.backintime.InvalidCmd':
                raise InvalidCmd(str(exc)) from exc

            elif exc._dbus_error_name == 'net.launchpad.backintime.LimitExceeded':
                raise LimitExceeded(str(exc))  from exc

            else:
                raise

    def save(self):
        """Save rules with serviceHelper.py after authentication.

        If no rules where added before this will delete current rule.
        """
        if not self.isReady:
            return

        try:
            return self.iface.save()

        except dbus.exceptions.DBusException as err:

            if err._dbus_error_name == 'com.ubuntu.DeviceDriver.PermissionDeniedByPolicy':
                raise PermissionDeniedByPolicy(str(err)) from err

            else:
                raise err

    def clean(self):
        """Clean up remote cache.
        """
        if not self.isReady:
            return

        self.iface.clean()


class PathHistory:
    def __init__(self, path):
        self.history = [path,]
        self.index = 0

    def append(self, path):
        #append path after the current index
        self.history = self.history[:self.index + 1] + [path,]
        self.index = len(self.history) - 1

    def previous(self):
        if self.index == 0:
            return self.history[0]
        try:
            path = self.history[self.index - 1]
        except IndexError:
            return self.history[self.index]
        self.index -= 1
        return path

    def next(self):
        if self.index == len(self.history) - 1:
            return self.history[-1]
        try:
            path = self.history[self.index + 1]
        except IndexError:
            return self.history[self.index]
        self.index += 1
        return path

    def reset(self, path):
        self.history = [path,]
        self.index = 0


class Execute:
    """Execute external commands and handle its output.

    Args:
        cmd (list): Command with arguments that should be called.
            The command will be called by  :py:class:`subprocess.Popen`.
        callback (method): Function which will handle output returned by
            command (e.g. to extract errors).
        user_data: Extra arguments which will be forwarded to ``callback``
            function (e.g. a ``tuple`` - which is passed by reference in
            Python - to "return" results of the callback function as side
            effect).
        filters (tuple): Tuple of functions used to filter messages before
            sending them to the ``callback`` function.
        parent (instance): Instance of the calling method used only to proper
            format log messages.
        conv_str (bool): Convert output to :py:class:`str` if ``True`` or keep
            it as :py:class:`bytes` if ``False``.
        join_stderr (bool): Join ``stderr`` to ``stdout``.

    Note:
        Signals ``SIGTSTP`` ("keyboard stop") and ``SIGCONT`` send to Python
        main process will be forwarded to the command. ``SIGHUP`` will kill
        the process.
    """
    def __init__(self,
                 cmd,
                 callback=None,
                 user_data=None,
                 filters=(),
                 parent=None,
                 conv_str=True,
                 join_stderr=True):
        self.cmd = cmd
        self.callback = callback
        self.user_data = user_data
        self.filters = filters
        self.currentProc = None
        self.conv_str = conv_str
        self.join_stderr = join_stderr
        # Need to forward parent to have the correct class name in debug log.
        self.parent = parent if parent else self

        # Dev note (buhtz, 2024-07): Previous version was calling os.system()
        # if cmd was a string instead of a list of strings. This is not secure
        # and to my knowledge and research also not used anymore in BIT.
        # It is my assumption that the RuntimeError will never be raised. But
        # let's keep it for some versions to be sure.
        if not isinstance(self.cmd, list):
            raise RuntimeError(
                'Command is a string but should be a list of strings. This '
                'method is not supported anymore since version 1.5.0. The '
                'current situation is unexpected. Please open a bug report '
                'at https://github.com/bit-team/backintime/issues/new/choose '
                'or report to the projects mailing list '
                '<bit-dev-join@python.org>.')

        self.pausable = True
        self.printable_cmd = ' '.join(self.cmd)
        logger.debug(f'Call command "{self.printable_cmd}"', self.parent, 2)

    def run(self):
        """Run the command using ``subprocess.Popen``.

        Returns:
            int: Code from the command.
        """
        ret_val = 0
        out = ''

        try:
            # register signals for pause, resume and kill
            # Forward these signals (sent to the "backintime" process
            # normally) to the child process ("rsync" normally).
            # Note: SIGSTOP (unblockable stop) cannot be forwarded because
            # it cannot be caught in a signal handler!
            signal.signal(signal.SIGTSTP, self.pause)
            signal.signal(signal.SIGCONT, self.resume)
            signal.signal(signal.SIGHUP, self.kill)

        except ValueError:
            # signal only work in qt main thread
            # TODO What does this imply?
            pass

        stderr = subprocess.STDOUT if self.join_stderr else subprocess.DEVNULL

        logger.debug(f"Starting command '{self.printable_cmd}'")

        self.currentProc = subprocess.Popen(
            self.cmd, stdout=subprocess.PIPE, stderr=stderr)

        # # TEST code for developers to simulate a killed rsync process
        # if self.printable_cmd.startswith("rsync --recursive"):
        #     self.currentProc.terminate()  # signal 15 (SIGTERM) like "killall" and "kill" do by default
        #     # self.currentProc.send_signal(signal.SIGHUP)  # signal 1
        #     # self.currentProc.kill()  # signal 9
        #     logger.error("rsync killed for testing purposes during development")

        if self.callback:

            for line in self.currentProc.stdout:

                if self.conv_str:
                    line = line.decode().rstrip('\n')
                else:
                    line = line.rstrip(b'\n')

                for f in self.filters:
                    line = f(line)

                if not line:
                    continue

                self.callback(line, self.user_data)

        # We use communicate() instead of wait() to avoid a deadlock
        # when stdout=PIPE and/or stderr=PIPE and the child process
        # generates enough output to pipe that it blocks waiting for
        # free buffer. See also:
        # https://docs.python.org/3.10/library/subprocess.html#subprocess.Popen.wait
        out = self.currentProc.communicate()[0]

        # TODO Why is "out" empty instead of containing all stdout?
        #      Most probably because Popen was called with a PIPE as stdout
        #      to directly process each stdout line by calling the callback...

        ret_val = self.currentProc.returncode
        # TODO ret_val is sometimes 0 instead of e.g. 23 for rsync. Why?

        try:
            # reset signal handler to their default
            signal.signal(signal.SIGTSTP, signal.SIG_DFL)
            signal.signal(signal.SIGCONT, signal.SIG_DFL)
            signal.signal(signal.SIGHUP, signal.SIG_DFL)
        except ValueError:
            # signal only work in qt main thread
            # TODO What does this imply?
            pass

        if ret_val == 0:
            msg = f'Command "{self.printable_cmd[:16]}" returns {ret_val}'
            if out:
                msg += ': ' + out.decode().strip('\n')
            logger.debug(msg, self.parent, 2)

        else:
            msg = f'Command "{self.printable_cmd}" ' \
                  f'returns {bcolors.WARNING}{ret_val}{bcolors.ENDC}'
            if out:
                msg += ' | ' + out.decode().strip('\n')
            logger.warning(msg, self.parent, 2)

        return ret_val

    def pause(self, signum, frame):
        """Slot which will send ``SIGSTOP`` to the command. Is connected to
        signal ``SIGTSTP``.
        """
        if self.pausable and self.currentProc:
            logger.info(
                f'Pause process "{self.printable_cmd}"', self.parent, 2)
            return self.currentProc.send_signal(signal.SIGSTOP)

    def resume(self, signum, frame):
        """Slot which will send ``SIGCONT`` to the command. Is connected to
        signal ``SIGCONT``.
        """
        if self.pausable and self.currentProc:
            logger.info(
                f'Resume process "{self.printable_cmd}"', self.parent, 2)
            return self.currentProc.send_signal(signal.SIGCONT)

    def kill(self, signum, frame):
        """Slot which will kill the command. Is connected to signal ``SIGHUP``.
        """
        if self.pausable and self.currentProc:
            logger.info(f'Kill process "{self.printable_cmd}"', self.parent, 2)
            return self.currentProc.kill()
