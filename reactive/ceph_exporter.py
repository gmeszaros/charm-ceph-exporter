import os
from charmhelpers import fetch
from charmhelpers.core import host, hookenv, unitdata
from charmhelpers.core.templating import render
from charms.reactive import (
    when, when_not, when_any, set_state, remove_state
)
from charms.reactive.helpers import any_file_changed, data_changed
from charmhelpers.contrib.network.ip import (
    get_address_in_network,
    get_ipv6_addr
)


SVCNAME = 'ceph_exporter'
PKGNAMES = ['ceph-exporter']
CONFIG_DEF = '/etc/default/ceph_exporter'
CONFIG_DEF_TMPL = 'etc_default_ceph-exporter.j2'

def get_network_addrs(config_opt):
    """Get all configured public networks addresses.

    If public network(s) are provided, go through them and return the
    addresses we have configured on any of those networks.
    """
    addrs = []
    networks = hookenv.config(config_opt)
    if networks:
        networks = networks.split()
        addrs = [get_address_in_network(n) for n in networks]
        addrs = [a for a in addrs if a]

    if not addrs:
        if networks:
            msg = ("Could not find an address on any of '%s' - resolve this "
                   "error to retry" % (networks))
            status_set('blocked', msg)
            raise Exception(msg)
        else:
            return [get_host_ip()]

    return addrs

def get_host_ip(hostname=None):
    if hookenv.config('prefer-ipv6'):
        return get_ipv6_addr()[0]

    hostname = hostname or unit_get('private-address')
    try:
        # Test to see if already an IPv4 address
        socket.inet_aton(hostname)
        return hostname
    except socket.error:
        # This may throw an NXDOMAIN exception; in which case
        # things are badly broken so just let it kill the hook
        answers = dns.resolver.query(hostname, 'A')
        if answers:
            return answers[0].address

def get_monitoring_addr():
    if hookenv.config('monitoring-network'):
        return get_network_addrs('monitoring-network')[0]

    try:
        return network_get_primary_address('private')
    except NotImplementedError:
        log("network-get not supported", DEBUG)

    return get_host_ip()



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
        target.configure(hostname=get_monitoring_addr(), port=config.get('port'))
    #networks = get_cluster_addr('monitoring-network')

# TODO: add relation broken and departed relations, remove application etc. cleanup
# service ceph_exporter stop
# apt purge ceph-exporter
# deconfigure prometheus port is done
# done: binds to wrong space, should be OAM, use monitoring-network
# add note missing relation:
# juju add-relation ceph-exporter:target proometheus:target
# add managed updates
