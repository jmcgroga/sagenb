# -*- coding: utf-8 -*
"""nodoctest
Server the Sage Notebook.
"""

#############################################################################
#       Copyright (C) 2009 William Stein <wstein@gmail.com>
#  Distributed under the terms of the GNU General Public License (GPL)
#  The full text of the GPL is available at:
#                  http://www.gnu.org/licenses/
#############################################################################

# From 5.0 forward, no longer supporting GnuTLS, so only use SSL protocol from OpenSSL
protocol = 'ssl'

# System libraries
import getpass
import os
import shutil
import socket
import sys
import hashlib
from exceptions import SystemExit

from twisted.python.runtime import platformType

from sagenb.misc.misc import (DOT_SAGENB, find_next_available_port,
                              print_open_msg)

import notebook

conf_path     = os.path.join(DOT_SAGENB, 'notebook')

private_pem   = os.path.join(conf_path, 'private.pem')
public_pem    = os.path.join(conf_path, 'public.pem')
template_file = os.path.join(conf_path, 'cert.cfg')

FLASK_NOTEBOOK_CONFIG = """
####################################################################        
# WARNING -- Do not edit this file!   It is autogenerated each time
# the notebook(...) command is executed.
# See http://twistedmatrix.com/documents/current/web/howto/using-twistedweb.html 
#  (Serving WSGI Applications) for the basic ideas of the below code
####################################################################
from twisted.internet import reactor

# Now set things up and start the notebook
import sagenb.notebook.notebook
sagenb.notebook.notebook.JSMATH=True
import sagenb.notebook.notebook as notebook
import sagenb.notebook.worksheet as worksheet

import sagenb.notebook.misc as misc

misc.DIR = %(cwd)r #We should really get rid of this!

import signal, sys, random
def save_notebook(notebook):
    from twisted.internet.error import ReactorNotRunning
    print "Quitting all running worksheets..."
    notebook.quit()
    print "Saving notebook..."
    notebook.save()
    print "Notebook cleanly saved."
    
def my_sigint(x, n):
    try:
        reactor.stop()
    except ReactorNotRunning:
        pass
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    
signal.signal(signal.SIGINT, my_sigint)

from twisted.web import server

#########
# Flask #
#########
import os
flask_dir = os.path.join(os.environ['SAGE_ROOT'], 'devel', 'sagenb', 'flask_version')
sys.path.append(flask_dir)
import base as flask_base
startup_token = '{0:x}'.format(random.randint(0, 2**128))
flask_app = flask_base.create_app(%(notebook_opts)s, startup_token=startup_token)
sys.path.remove(flask_dir)

from twisted.web.wsgi import WSGIResource
resource = WSGIResource(reactor, reactor.getThreadPool(), flask_app)

class QuietSite(server.Site):
    def log(*args, **kwargs):
        "Override the logging so that requests are not logged"
        pass

# Log only errors, not every page hit
site = QuietSite(resource)

# To log every single page hit, uncomment the following line
#site = server.Site(resource)

from twisted.application import service, strports
application = service.Application("Sage Notebook")
s = strports.service(%(strport)r, site)
%(open_page)s
s.setServiceParent(application)

#This has to be done after flask_base.create_app is run
from functools import partial
reactor.addSystemEventTrigger('before', 'shutdown', partial(save_notebook, flask_base.notebook))
"""

def cmd_exists(cmd):
    """
    Return True if the given cmd exists.
    """
    return os.system('which %s 2>/dev/null >/dev/null' % cmd) == 0

def get_old_settings(conf):
    """
    Returns three settings from the Twisted configuration file conf:
    the interface, port number, and whether the server is secure.  If
    there are any errors, this returns (None, None, None).
    """
    import re
    # This should match the format written to twistedconf.tac below.
    p = re.compile(r'interface="(.*)",port=(\d*),secure=(True|False)')
    try:
        interface, port, secure = p.search(open(conf, 'r').read()).groups()
        if secure == 'True':
            secure = True
        else:
            secure = False
        return interface, port, secure
    except IOError, AttributeError:
        return None, None, None

def notebook_setup(self=None):
    if not os.path.exists(conf_path):
        os.makedirs(conf_path)

    if not cmd_exists('certtool'):
        raise RuntimeError("You must install certtool to use the secure notebook server.")

    dn = raw_input("Domain name [localhost]: ").strip()
    if dn == '':
        print "Using default localhost"
        dn = 'localhost'

    import random
    template_dict = {'organization': 'SAGE (at %s)' % (dn),
                'unit': '389',
                'locality': None,
                'state': 'Washington',
                'country': 'US',
                'cn': dn,
                'uid': 'sage_user',
                'dn_oid': None,
                'serial': str(random.randint(1, 2 ** 31)),
                'dns_name': None,
                'crl_dist_points': None,
                'ip_address': None,
                'expiration_days': 10000,
                'email': 'sage@sagemath.org',
                'ca': None,
                'tls_www_client': None,
                'tls_www_server': True,
                'signing_key': True,
                'encryption_key': True,
                }
                
    s = ""
    for key, val in template_dict.iteritems():
        if val is None:
            continue
        if val == True:
            w = ''
        elif isinstance(val, list):
            w = ' '.join(['"%s"' % x for x in val])
        else:
            w = '"%s"' % val
        s += '%s = %s \n' % (key, w) 

    f = open(template_file, 'w')
    f.write(s)
    f.close()

    import subprocess

    if os.uname()[0] != 'Darwin' and cmd_exists('openssl'):
        # We use openssl by default if it exists, since it is open
        # *vastly* faster on Linux, for some weird reason.
        cmd = ['openssl genrsa > %s' % private_pem]
        print "Using openssl to generate key"
        print cmd[0]
        subprocess.call(cmd, shell=True)
    else:
        # We checked above that certtool is available.
        cmd = ['certtool --generate-privkey --outfile %s' % private_pem]
        print "Using certtool to generate key"
        print cmd[0]
        subprocess.call(cmd, shell=True)

    cmd = ['certtool --generate-self-signed --template %s --load-privkey %s '
           '--outfile %s' % (template_file, private_pem, public_pem)]
    print cmd[0]
    subprocess.call(cmd, shell=True)
    
    # Set permissions on private cert
    os.chmod(private_pem, 0600)

    print "Successfully configured notebook."

def notebook_twisted(self,
             directory     = None,
             port          = 8080,
             interface     = 'localhost',
             address       = None,
             port_tries    = 50,
             secure        = False,
             reset         = False,
             require_login = True,
             accounts      = None,
             openid        = None,

             server_pool   = None,
             ulimit        = '',

             timeout       = 0,

             upload        = None,
             open_viewer   = True,

             sagetex_path  = "",
             start_path    = "",
             fork          = False,
             quiet         = False,
             subnets = None):

    if subnets is not None:
        raise ValueError("""The subnets parameter is no longer supported. Please use a firewall to block subnets, or even better, volunteer to write the code to implement subnets again.""")

    cwd = os.getcwd()
    # For backwards compatible, we still allow the address to be set
    # instead of the interface argument
    if address is not None:
        from warnings import warn
        message = "Use 'interface' instead of 'address' when calling notebook(...)."
        warn(message, DeprecationWarning, stacklevel=3)
        interface = address

    if directory is None:
        directory = '%s/sage_notebook' % DOT_SAGENB
    else:
        if (isinstance(directory, basestring) and len(directory) > 0 and
           directory[-1] == "/"):
            directory = directory[:-1]

    # First change to the directory that contains the notebook directory
    wd = os.path.split(directory)
    if wd[0]:
        os.chdir(wd[0])
    directory = wd[1]

    port = int(port)

    if not secure and interface != 'localhost':
        print '*' * 70
        print "WARNING: Running the notebook insecurely not on localhost is dangerous"
        print "because its possible for people to sniff passwords and gain access to"
        print "your account. Make sure you know what you are doing."
        print '*' * 70

    # first use provided values, if none, use loaded values, 
    # if none use defaults

    nb = notebook.load_notebook(directory)
    
    directory = nb._dir
    conf = os.path.join(directory, 'twistedconf.tac')
    
    if not quiet:
        print "The notebook files are stored in:", nb._dir

    nb.conf()['idle_timeout'] = int(timeout)
    nb.conf()['require_login'] = require_login

    if openid is not None:
        nb.conf()['openid'] = openid 
    elif not nb.conf()['openid']:
        nb.conf()['openid'] = False

    if accounts is not None:
        nb.user_manager().set_accounts(accounts)
    elif not nb.conf()['accounts']:
        nb.user_manager().set_accounts(True)
    
    if nb.user_manager().user_exists('root') and not nb.user_manager().user_exists('admin'):
        # This is here only for backward compatibility with one
        # version of the notebook.
        s = nb.create_user_with_same_password('admin', 'root')
        # It would be a security risk to leave an escalated account around.

    if not nb.user_manager().user_exists('admin'):
        reset = True
        
    if reset:
        passwd = get_admin_passwd()                
        if reset:
            admin = nb.user_manager().user('admin')
            admin.set_password(passwd)
            print "Password changed for user 'admin'."
        else:
            nb.user_manager().create_default_users(passwd)
            print "User admin created with the password you specified."
            print "\n\n"
            print "*" * 70
            print "\n"
            if secure:
                print "Login to the Sage notebook as admin with the password you specified above."
        #nb.del_user('root')
            
    nb.set_server_pool(server_pool)
    nb.set_ulimit(ulimit)
    
    if os.path.exists('%s/nb-older-backup.sobj' % directory):
        nb._migrate_worksheets()
        os.unlink('%s/nb-older-backup.sobj' % directory)
        print "Updating to new format complete."


    nb.upgrade_model()

    nb.save()
    del nb

    def run(port):
        # Is a server already running? Check if a Twistd PID exists in
        # the given directory.
        pidfile = os.path.join(directory, 'twistd.pid')
        if platformType != 'win32':
            from twisted.scripts._twistd_unix import checkPID
            try:
                checkPID(pidfile)
            except SystemExit as e:
                pid = int(open(pidfile).read())

                if str(e).startswith('Another twistd server is running,'):
                    print 'Another Sage Notebook server is running, PID %d.' % pid
                    old_interface, old_port, old_secure = get_old_settings(conf)
                    if old_port and (open_viewer or upload):
                        old_interface = old_interface or 'localhost'

                        startpath = '/'
                        if upload:
                            import urllib
                            startpath = '/upload_worksheet?url=file://%s' % (urllib.quote(upload))

                        print 'Opening web browser at http%s://%s:%s/ ...' % (
                            's' if old_secure else '', old_interface, old_port)

                        from sagenb.misc.misc import open_page as browse_to
                        browse_to(old_interface, old_port, old_secure, startpath)
                        return
                    print '\nPlease either stop the old server or run the new server in a different directory.'
                    return

        ## Create the config file
        if secure:
            if (not os.path.exists(private_pem) or
                not os.path.exists(public_pem)):
                print "In order to use an SECURE encrypted notebook, you must first run notebook.setup()."
                print "Now running notebook.setup()"
                notebook_setup()
            if (not os.path.exists(private_pem) or
                not os.path.exists(public_pem)):
                print "Failed to setup notebook.  Please try notebook.setup() again manually."
            strport = '%s:%s:interface=%s:privateKey=%s:certKey=%s'%(
                protocol, port, interface, private_pem, public_pem)
        else:
            strport = 'tcp:%s:interface=%s' % (port, interface)

        notebook_opts = '"%s",interface="%s",port=%s,secure=%s' % (
            os.path.abspath(directory), interface, port, secure)

        if open_viewer or upload:
            if require_login:
                start_path = "'/?startup_token=%s' % startup_token"
            elif upload:
                start_path = "'/upload_worksheet?url=file://%s'" % upload
            else:
                start_path = "'/'"
            if interface:
                hostname = interface
            else:
                hostname = 'localhost'
            open_page = "from sagenb.misc.misc import open_page; open_page('%s', %s, %s, %s);" % (hostname, port, secure, start_path)
            # If we have to login and upload a file, then we do them
            # in that order and hope that the login is fast enough.
            if require_login and upload:
                import urllib
                open_page += "open_page('%s', %s, %s, '/upload_worksheet?url=file://%s');" % (hostname, port, secure, urllib.quote(upload))

        else:
            open_page = ''

        config = open(conf, 'w')

        config.write(FLASK_NOTEBOOK_CONFIG%{'notebook_opts': notebook_opts, 'sagetex_path': sagetex_path,
                                            'do_not_require_login': not require_login,
                                            'dir': os.path.abspath(directory), 'cwd':cwd, 
                                            'strport': strport,
                                            'open_page': open_page})


        config.close()                     

        ## Start up twisted
        cmd = 'twistd --pidfile="%s" -ny "%s"' % (pidfile, conf)
        if not quiet:
            print_open_msg('localhost' if not interface else interface,
            port, secure=secure)
        if secure and not quiet:
            print "There is an admin account.  If you do not remember the password,"
            print "quit the notebook and type notebook(reset=True)."

        if fork:
            import pexpect
            return pexpect.spawn(cmd)
        else:
            e = os.system(cmd)

        os.chdir(cwd)
        if e == 256:
            raise socket.error

        return True
        # end of inner function run
                     
    if interface != 'localhost' and not secure:
            print "*" * 70
            print "WARNING: Insecure notebook server listening on external interface."
            print "Unless you are running this via ssh port forwarding, you are"
            print "**crazy**!  You should run the notebook with the option secure=True."
            print "*" * 70

    port = find_next_available_port(interface, port, port_tries)
    if open_viewer:
        "Open viewer automatically isn't fully implemented.  You have to manually open your web browser to the above URL."
    return run(port)

def get_admin_passwd():
    print "\n" * 2
    print "Please choose a new password for the Sage Notebook 'admin' user."
    print "Do _not_ choose a stupid password, since anybody who could guess your password"
    print "and connect to your machine could access or delete your files."
    print "NOTE: Only the md5 hash of the password you type is stored by Sage."
    print "You can change your password by typing notebook(reset=True)."
    print "\n" * 2
    while True:
        passwd = getpass.getpass("Enter new password: ")
        from sagenb.misc.misc import min_password_length
        if len(passwd) < min_password_length:
            print "That password is way too short. Enter a password with at least 6 characters."
            continue
        passwd2 = getpass.getpass("Retype new password: ")
        if passwd != passwd2:
            print "Sorry, passwords do not match."
        else:
            break

    print "Please login to the notebook with the username 'admin' and the above password."
    return passwd
