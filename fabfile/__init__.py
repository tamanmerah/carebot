import app_config
import copy
from fabric.api import *
from fabric.state import env
from jinja2 import Template
import logging

import carebot


"""
General configuration
"""

env.user = app_config.SERVER_USER
env.hosts = app_config.SERVERS
env.slug = app_config.PROJECT_SLUG

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

env.user = app_config.SERVER_USER
env.forward_agent = True

env.hosts = []
env.settings = None


"""
Configuration
"""
def _get_template_conf_path(service, extension):
    """
    Derive the path for a conf template file.
    """
    return 'confs/%s.%s' % (service, extension)

def _get_rendered_conf_path(service, extension):
    """
    Derive the rendered path for a conf file.
    """
    return 'confs/rendered/%s.%s.%s' % (app_config.PROJECT_FILENAME, service, extension)

def _get_installed_conf_path(service, remote_path, extension):
    """
    Derive the installed path for a conf file.
    """
    return '/etc/init/%s.%s.%s' % (app_config.PROJECT_FILENAME, service, extension)

def _get_installed_service_name(service):
    """
    Derive the init service name for an installed service.
    """
    return '%s.%s' % (app_config.PROJECT_FILENAME, service)


"""
Running the app
Probably only neded the first time, to set up oauth creds
"""
@task
def app(port='8000'):
    """
    Serve app.py.
    """
    if env.settings:
        local("DEPLOYMENT_TARGET=%s bash -c 'gunicorn -b 0.0.0.0:%s --timeout 3600 --debug --reload app:wsgi_app'" % (env.settings, port))
    else:
        local('gunicorn -b 0.0.0.0:%s --timeout 3600 --debug --reload app:wsgi_app' % port)


"""
Environments
"""
@task
def production():
    """
    Run as though on production.
    """
    env.settings = 'production'
    env.branch = 'master'
    app_config.configure_targets(env.settings)
    env.hosts = app_config.SERVERS

@task
def staging():
    """
    Run as though on staging.
    """
    env.settings = 'staging'
    env.branch = 'master'
    app_config.configure_targets(env.settings)
    env.hosts = app_config.SERVERS

"""
Branches
"""
@task
def branch(branch_name):
    """
    Work on any specified branch.
    """
    env.branch = branch_name

@task
def create_directories():
    """
    Create server directories.
    """
    require('settings', provided_by=['production', 'staging'])

    run('mkdir -p %(SERVER_PROJECT_PATH)s' % app_config.__dict__)
    run('git clone %(REPOSITORY_URL)s %(SERVER_PROJECT_PATH)s' % app_config.__dict__)

@task
def setup_logs():
    """
    Create log directories.
    """
    require('settings', provided_by=['production', 'staging'])

    sudo('mkdir %(SERVER_LOG_PATH)s' % app_config.__dict__)
    sudo('chown ubuntu:ubuntu %(SERVER_LOG_PATH)s' % app_config.__dict__)

@task
def create_virtualenv():
    """
    Setup a server virtualenv.
    """
    require('settings', provided_by=['production', 'staging'])

    run('virtualenv -p %(SERVER_PYTHON)s %(SERVER_VIRTUALENV_PATH)s' % app_config.__dict__)
    run('source %(SERVER_VIRTUALENV_PATH)s/bin/activate' % app_config.__dict__)


@task
def clone_repo():
    """
    Clone the source repository.
    """
    require('settings', provided_by=['production', 'staging'])

    run('git clone %(REPOSITORY_URL)s %(SERVER_REPOSITORY_PATH)s' % app_config.__dict__)

    if app_config.REPOSITORY_ALT_URL:
        run('git remote add bitbucket %(REPOSITORY_ALT_URL)s' % app_config.__dict__)

"""
fab migration:name='201603241327_add_team'
"""
@task
def migration(name):
    run('source %(SERVER_VIRTUALENV_PATH)s/bin/activate' % app_config.__dict__)
    run('python %s/migrations/%s.py' % (app_config.__dict__['SERVER_PROJECT_PATH'], name))

@task
def checkout_latest(remote='origin'):
    """
    Get the updated code
    """
    run('cd %s; git fetch %s' % (app_config.SERVER_PROJECT_PATH, remote))
    run('cd %s; git checkout %s; git pull %s %s' % (app_config.SERVER_PROJECT_PATH, env.branch, remote, env.branch))


@task
def install_requirements():
    """
    Install the latest requirements.
    """
    require('settings', provided_by=['production', 'staging'])

    run('%(SERVER_VIRTUALENV_PATH)s/bin/pip install -U -r %(SERVER_PROJECT_PATH)s/requirements.txt' % app_config.__dict__)
    # run('cd %(SERVER_REPOSITORY_PATH)s; npm install' % app_config.__dict__)


@task
def render_confs():
    """
    Renders server configurations.
    """
    require('settings', provided_by=['production', 'staging'])

    with settings(warn_only=True):
        local('rm -rf confs/rendered')
        local('mkdir confs/rendered')

    # Copy the app_config so that when we load the secrets they don't
    # get exposed to other management commands
    context = copy.copy(app_config.__dict__)
    context.update(app_config.get_secrets())

    for service, remote_path, extension in app_config.SERVER_SERVICES:
        template_path = _get_template_conf_path(service, extension)
        rendered_path = _get_rendered_conf_path(service, extension)

        with open(template_path,  'r') as read_template:

            with open(rendered_path, 'wb') as write_template:
                payload = Template(read_template.read())
                write_template.write(payload.render(**context))

@task
def deploy_confs():
    """
    Deploys rendered server configurations to the specified server.
    This will reload nginx and the appropriate uwsgi config.
    """
    require('settings', provided_by=['production', 'staging'])

    put('%s.env' % env.settings, '%(SERVER_PROJECT_PATH)s/.env' % app_config.__dict__)
    # TODO -- we might want to run `source .env`?

    render_confs()

    with settings(warn_only=True):
        for service, remote_path, extension in app_config.SERVER_SERVICES:
            rendered_path = _get_rendered_conf_path(service, extension)
            installed_path = _get_installed_conf_path(service, remote_path, extension)

            print 'Updating %s' % installed_path
            put(rendered_path, installed_path, use_sudo=True)

            sudo('initctl reload-configuration')

            if service == 'nginx':
                sudo('service nginx reload')
            else:
                service_name = _get_installed_service_name(service)
                sudo('service %s restart' % service_name)


@task
def deploy_analytics_conf():
    # Move google ouath credentials
    local('cp ~/.google_oauth_credentials ./.google_oauth_credentials')
    put('.google_oauth_credentials', '~/.google_oauth_credentials')
    put('.google_oauth_credentials', '/root/.google_oauth_credentials', use_sudo=True)

    # run('mkdir -p %(SERVER_PROJECT_PATH)s' % app_config.__dict__)

@task
def install_crontab():
    """
    Install cron jobs script into cron.d.
    """
    require('settings', provided_by=['production', 'staging'])

    sudo('cp %(SERVER_PROJECT_PATH)s/crontab /etc/cron.d/%(PROJECT_FILENAME)s' % app_config.__dict__)

@task
def setup_database():
    """
    Manually create an empty sqlite DB.
    Otherwise it gets created by root on first run, and regular
    users can't write to it.
    """
    sudo('sqlite3 %(SERVER_PROJECT_PATH)s/%(PROJECT_FILENAME)s.db ".databases"' % app_config.__dict__)
    sudo('chown ubuntu:ubuntu %(SERVER_PROJECT_PATH)s/%(PROJECT_FILENAME)s.db' % app_config.__dict__)

@task
def start_service(service):
    """
    Start a service on the server.
    """
    service_name = _get_installed_service_name(service)
    sudo('service %s start' % service_name)

@task
def stop_service(service):
    """
    Stop a service on the server
    """
    service_name = _get_installed_service_name(service)
    sudo('service %s stop' % service_name)

@task
def setup():
    require('settings', provided_by=['production', 'staging'])

    setup_logs()
    create_directories()
    create_virtualenv()
    clone_repo()
    checkout_latest()
    setup_database()
    install_requirements()
    deploy_analytics_conf()
    deploy_confs()
    install_crontab()

@task
def reboot():
    """
    Restart the server
    TKTK
    """
    None

@task
def deploy():
    require('settings', provided_by=['production', 'staging'])

    with settings(warn_only=True):
        stop_service('bot')

    checkout_latest()
    install_requirements()
    deploy_analytics_conf()
    render_confs()
    deploy_confs()
    install_crontab()

"""
Deaths, destroyers of worlds
"""
@task
def shiva_the_destroyer():
    """
    Remove all directories, databases, etc. associated with the application.
    """
    with settings(warn_only=True):
        run('rm -Rf %(SERVER_PROJECT_PATH)s' % app_config.__dict__)
        run('rm -Rf %(SERVER_VIRTUALENV_PATH)s' % app_config.__dict__)
        sudo('rm -Rf %(SERVER_LOG_PATH)s' % app_config.__dict__)

        # Remove any installed services
        stop_service('bot')
        installed_service_path = _get_installed_conf_path(service, remote_path, extension)
        sudo('rm -f %s' % installed_service_path)
