import os
from charmhelpers import fetch
from charmhelpers.core import host, hookenv, unitdata
from charmhelpers.core.templating import render
from charms.reactive import (
    when, when_not, when_any, set_state, remove_state
)
from charms.reactive import hook
from charms.reactive.helpers import any_file_changed, data_changed
from charmhelpers.contrib.openstack.ip import (
    resolve_address,
    INTERNAL
)

SVCNAME = 'ceph_exporter'
PKGNAMES = ['ceph-exporter']
CONFIG_DEF = '/etc/default/ceph_exporter'
CONFIG_DEF_TMPL = 'etc_default_ceph-exporter.j2'

def templates_changed(tmpl_list):
    return any_file_changed(['templates/{}'.format(x) for x in tmpl_list])


@when('ceph-exporter.do-install')
def install_packages():
    fetch.configure_sources()
    fetch.apt_update()
    fetch.apt_install(PKGNAMES, fatal=True)
    remove_state('ceph-exporter.do-install')


def runtime_args(key=None, value=None):
    kv = unitdata.kv()
    args = kv.get('runtime_args', {})
    if key:
        args.update({key: value})
        kv.set('runtime_args', args)
    args_list = ['{}={}'.format(k, v) for k, v in args.items() if v]
    # sorted list is needed to avoid data_changed() false-positives
    return sorted(args_list)


@when('ceph-exporter.do-reconfig-def')
def write_ceph_exporter_config_def():
    config = hookenv.config()
    if config.get('ceph.config'):
        runtime_args('CEPH_CONFIG','\'{}\'     # path to ceph config file'.format(config['ceph.config']))
    if config.get('ceph.user'):
        runtime_args('CEPH_USER','\'{}\'       # Ceph user to connect to cluster. (default "admin")'.format(config['ceph.user']))
    if config.get('exporter.config'):
        runtime_args('EXPORTER_CONFIG','\'{}\' # Path to ceph exporter config. (default "/etc/ceph/exporter.yml")'.format(config['exporter.config']))
    if config.get('port', False):
        if config.get('telemetry.addr'):
            runtime_args('TELEMETRY_ADDR','\'{}:{}\'  # host:port for ceph exporter (default ":9128")'.format(config['telemetry.addr'], config['port']))
        else:
            runtime_args('TELEMETRY_ADDR','\':{}\'  # host:port for ceph exporter (default ":9128")'.format(config['port']))
    if config.get('telemetry.path'):
        runtime_args('TELEMETRY_PATH','\'{}\'  # URL path for surfacing collected metrics (default "/metrics")'.format(config['telemetry.path']))
    args = runtime_args()
    hookenv.log('runtime_args: {}'.format(args))
    if args:
        render(source=CONFIG_DEF_TMPL,
               target=CONFIG_DEF,
               context={'args': args},
               )
    set_state('ceph-exporter.do-restart')
    if any((
        data_changed('ceph-exporter.args', args),
        templates_changed([CONFIG_DEF_TMPL]),
    )):
        set_state('ceph-exporter.do-reconfig-def')

    remove_state('ceph-exporter.do-reconfig-def')


@when_not('ceph-exporter.started')
def setup_ceph_exporter():
    hookenv.status_set('maintenance', 'Installing software')
    install_packages()
    set_state('ceph-exporter.do-check-reconfig')


@when_any('ceph-exporter.started', 'ceph-exporter.do-check-reconfig')
def check_reconfig_ceph_exporter():
    config = hookenv.config()
    args = runtime_args()
    install_opts = ('install_sources', 'install_keys')
    if any(config.changed(opt) for opt in install_opts):
        set_state('ceph-exporter.do-install')

    if data_changed('ceph-exporter.config', config):
        set_state('ceph-exporter.do-reconfig-def')

    remove_state('ceph-exporter.do-check-reconfig')


@when('ceph-exporter.do-restart')
def restart_ceph_exporter():
    if not host.service_running(SVCNAME):
        hookenv.log('Starting {}...'.format(SVCNAME))
        host.service_start(SVCNAME)
    else:
        hookenv.log('Restarting {}, config file changed...'.format(SVCNAME))
        host.service_restart(SVCNAME)
    hookenv.status_set('active', 'Ready')
    set_state('ceph-exporter.started')
    remove_state('ceph-exporter.do-restart')


# Relations
@when('ceph-exporter.started')
@when('target.available')
def configure_ceph_exporter_relation(target):
    config = hookenv.config()
    if data_changed('target.config', config):
        target.configure(hostname=resolve_address(endpoint_type=INTERNAL), port=config.get('port'))
        hookenv.status_set('active', 'Ready')

@when('ceph-exporter.started')
@when_not('target.available')
def setup_target_relation():
    hookenv.status_set('waiting', 'Waiting for: prometheus')

@hook('stop')
def hook_handler_stop():
    set_state('ceph-exporter.stopped')

@when('ceph-exporter.stopped')
def remove_packages():
   fetch.apt_purge(PKGNAMES, fatal=True)
