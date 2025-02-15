# Copyright 2021 Max Planck Institute for Software Systems, and
# National University of Singapore
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import math

class Simulator(object):
    def __init__(self):
        self.extra_deps = []

    # number of cores required for this simulator
    def resreq_cores(self):
        return 1

    # memory required for this simulator (in MB)
    def resreq_mem(self):
        return 64

    def prep_cmds(self, env):
        return []

    def run_cmd(self, env):
        return None

    # Other simulators this one depends on
    def dependencies(self):
        return []

    # Sockets to be cleaned up
    def sockets_cleanup(self, env):
        return []

    # sockets to wait for indicating the simulator is ready
    def sockets_wait(self, env):
        return []

    def start_delay(self):
        return 5

    def wait_terminate(self):
        return False

class HostSim(Simulator):
    node_config = None
    name = ''
    wait = False
    sleep = 0
    cpu_freq = '8GHz'

    sync_mode = 0
    sync_period = 500
    pci_latency = 500

    def __init__(self):
        self.pcidevs = []
        super().__init__()

    def full_name(self):
        return 'host.' + self.name

    def add_nic(self, dev):
        self.add_pcidev(dev)

    def add_pcidev(self, dev):
        dev.name = self.name + '.' + dev.name
        self.pcidevs.append(dev)

    def set_config(self, nc):
        self.node_config = nc

    def dependencies(self):
        deps = []
        for dev in self.pcidevs:
            deps.append(dev)
            if isinstance(dev, NICSim):
                deps.append(dev.network)
        return deps

    def wait_terminate(self):
        return self.wait


class PCIDevSim(Simulator):
    name = ''
    sync_mode = 0
    start_tick = 0
    sync_period = 500
    pci_latency = 500

    def __init__(self):
        super().__init__()

    def full_name(self):
        return 'dev.' + self.name

    def sockets_cleanup(self, env):
        return [env.dev_pci_path(self), env.dev_shm_path(self)]

    def sockets_wait(self, env):
        return [env.dev_pci_path(self)]


class NICSim(PCIDevSim):
    network = None
    eth_latency = 500

    def __init__(self):
        super().__init__()

    def set_network(self, net):
        self.network = net
        net.nics.append(self)

    def basic_args(self, env, extra=None):
        cmd = '%s %s %s %d %d %d %d %d' % \
            (env.dev_pci_path(self), env.nic_eth_path(self),
                    env.dev_shm_path(self), self.sync_mode, self.start_tick,
                    self.sync_period, self.pci_latency, self.eth_latency)

        if extra is not None:
            cmd += ' ' + extra
        return cmd

    def basic_run_cmd(self, env, name, extra=None):
        cmd = '%s/%s %s' % \
            (env.repodir + '/sims/nic', name,
             self.basic_args(env, extra))
        return cmd

    def full_name(self):
        return 'nic.' + self.name

    def sockets_cleanup(self, env):
        return super().sockets_cleanup(env) + [env.nic_eth_path(self)]

    def sockets_wait(self, env):
        return super().sockets_wait(env) + [env.nic_eth_path(self)]

class NetSim(Simulator):
    name = ''
    opt = ''
    sync_mode = 0
    sync_period = 500
    eth_latency = 500

    def __init__(self):
        self.nics = []
        self.net_listen = []
        self.net_connect = []
        super().__init__()

    def full_name(self):
        return 'net.' + self.name

    # Connect this netwrok to the listening peer `net`
    def connect_network(self, net):
        net.net_listen.append(self)
        self.net_connect.append(net)

    def connect_sockets(self, env):
        sockets = []
        for n in self.nics:
            sockets.append((n, env.nic_eth_path(n)))
        for n in self.net_connect:
            sockets.append((n, env.n2n_eth_path(n, self)))
        return sockets

    def listen_sockets(self, env):
        listens = []
        for net in self.net_listen:
            listens.append((net, env.n2n_eth_path(self, net)))
        return listens

    def dependencies(self):
        return self.nics + self.net_connect

    def sockets_cleanup(self, env):
        return [s for (_,s) in self.listen_sockets(env)]

    def sockets_wait(self, env):
        return [s for (_,s) in self.listen_sockets(env)]


class QemuHost(HostSim):
    sync = False
    cpu_freq = '4GHz'

    def __init__(self):
        super().__init__()

    def resreq_cores(self):
        if self.sync:
            return 1
        else:
            return self.node_config.cores + 1

    def resreq_mem(self):
        return 8192

    def prep_cmds(self, env):
        to_path = env.hdcopy_path(self)
        return [f'{env.qemu_img_path} create -f qcow2 -o '
            f'backing_file="{env.hd_path(self.node_config.disk_image)}" '
            f'{env.hdcopy_path(self)}']

    def run_cmd(self, env):
        cmd = (f'{env.qemu_path} -machine q35 -serial mon:stdio '
            '-display none -nic none '
            f'-kernel {env.qemu_kernel_path} '
            f'-drive file={env.hdcopy_path(self)},if=ide,index=0,media=disk '
            f'-drive file={env.cfgtar_path(self)},if=ide,index=1,media=disk,'
                'driver=raw '
            '-append "earlyprintk=ttyS0 console=ttyS0 root=/dev/sda1 '
                'init=/home/ubuntu/guestinit.sh rw" '
            f'-m {self.node_config.memory} -smp {self.node_config.cores} ')

        if self.sync:
            unit = self.cpu_freq[-3:]
            if unit.lower() == 'ghz':
                base = 0
            elif unit.lower() == 'mhz':
                base = 3
            else:
                raise Exception('cpu frequency specified in unsupported unit')
            num = float(self.cpu_freq[:-3])
            shift = base - int(math.ceil(math.log(num, 2)))

            cmd += f' -cpu Skylake-Server -icount shift={shift},sleep=off '
        else:
            cmd += ' -cpu host -enable-kvm '

        di = 0
        for dev in self.pcidevs:
            cmd += f'-chardev socket,path={env.dev_pci_path(dev)},'
            cmd += f'id=simbrickscd{di} '
            cmd += f'-device simbricks-pci,chardev=simbrickscd{di}'
            if self.sync:
                cmd += ',sync=on'
                cmd += f',sync-mode={self.sync_mode}'
                cmd += f',pci-latency={self.pci_latency}'
                cmd += f',sync-period={self.sync_period}'
            else:
                cmd += ',sync=off'
            cmd += ' '
            di += 1

        return cmd

class Gem5Host(HostSim):
    cpu_type_cp = 'X86KvmCPU'
    cpu_type = 'TimingSimpleCPU'
    sys_clock  = '1GHz'

    def __init__(self):
        super().__init__()

    def set_config(self, nc):
        nc.sim = 'gem5'
        super().set_config(nc)

    def resreq_cores(self):
        return 1

    def resreq_mem(self):
        return 4096

    def prep_cmds(self, env):
        return [f'mkdir -p {env.gem5_cpdir(self)}']

    def run_cmd(self, env):
        cpu_type = self.cpu_type
        if env.create_cp:
            cpu_type = self.cpu_type_cp

        cmd = (f'{env.gem5_path} --outdir={env.gem5_outdir(self)} '
            f'{env.gem5_py_path} --caches --l2cache --l3cache '
            '--l1d_size=32kB --l1i_size=32kB --l2_size=2MB --l3_size=32MB '
            '--l1d_assoc=8 --l1i_assoc=8 --l2_assoc=4 --l3_assoc=16 '
            f'--cacheline_size=64 --cpu-clock={self.cpu_freq} --sys-clock={self.sys_clock} '
            f'--checkpoint-dir={env.gem5_cpdir(self)} '
            f'--kernel={env.gem5_kernel_path} '
            f'--disk-image={env.hd_raw_path(self.node_config.disk_image)} '
            f'--disk-image={env.cfgtar_path(self)} '
            f'--cpu-type={cpu_type} --mem-size={self.node_config.memory}MB '
            f'--num-cpus={self.node_config.cores} '
            '--ddio-enabled --ddio-way-part=8 --mem-type=DDR4_2400_16x4 ')

        if (env.no_simbricks):
            cmd += '--no-simbricks '

        if env.create_cp:
            cmd += '--max-checkpoints=1 '

        if env.restore_cp:
            cmd += '-r 1 '

        if len(self.pcidevs) > 0:
            assert len(self.pcidevs) == 1 # our gem5 python script supports only 1
            dev = self.pcidevs[0]
            cmd += f'--simbricks-pci={env.dev_pci_path(dev)} '
            cmd += f'--simbricks-shm={env.dev_shm_path(dev)} '
            if cpu_type == 'TimingSimpleCPU':
                cmd +=  '--simbricks-sync '
                cmd += f'--simbricks-sync_mode={self.sync_mode} '
                cmd += f'--simbricks-pci-lat={self.pci_latency} '
                cmd += f'--simbricks-sync-int={self.sync_period} '
            if isinstance(dev, I40eNIC) or \
                    (isinstance(dev, MultiSubNIC) and \
                     isinstance(dev.multinic, I40eMultiNIC)):
                cmd += '--simbricks-type=i40e '
            elif isinstance(dev, FEMUDev):
                cmd += '--simbricks-type=femu '
        return cmd



class CorundumVerilatorNIC(NICSim):
    clock_freq = 250 # MHz

    def __init__(self):
        super().__init__()

    def resreq_mem(self):
        # this is a guess
        return 512

    def run_cmd(self, env):
        return self.basic_run_cmd(env, '/corundum/corundum_verilator',
            str(self.clock_freq))

class CorundumBMNIC(NICSim):
    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        return self.basic_run_cmd(env, '/corundum_bm/corundum_bm')

class I40eNIC(NICSim):
    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        return self.basic_run_cmd(env, '/i40e_bm/i40e_bm')

class E1000NIC(NICSim):
    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        #return 'valgrind -v -v -v ' + self.basic_run_cmd(env, '/e1000_gem5/e1000_gem5')
        return self.basic_run_cmd(env, '/e1000_gem5/e1000_gem5')

class MultiSubNIC(NICSim):
    name = ''
    multinic = None

    def __init__(self, mn):
        self.multinic = mn
        super().__init__()

    def full_name(self):
        return self.multinic.full_name() + '.' + self.name

    def dependencies(self):
        return super().dependencies() + [self.multinic]

    def start_delay(self):
        return 0

class I40eMultiNIC(Simulator):
    def __init__(self):
        self.subnics = []
        super().__init__()

    def create_subnic(self):
        sn = MultiSubNIC(self)
        self.subnics.append(sn)
        return sn

    def full_name(self):
        return 'multinic.' + self.name

    def run_cmd(self, env):
        args = ''
        first = True
        for sn in self.subnics:
            if not first:
                args += ' -- '
            first = False
            args += sn.basic_args(env)
        return '%s/sims/nic/i40e_bm/i40e_bm %s' % (env.repodir, args)

    def sockets_cleanup(self, env):
        ss = []
        for sn in self.subnics:
            ss += sn.sockets_cleanup(env)
        return ss

    def sockets_wait(self, env):
        ss = []
        for sn in self.subnics:
            ss += sn.sockets_wait(env)
        return ss


class WireNet(NetSim):
    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        connects = self.connect_sockets()
        assert len(connects) == 2
        cmd = '%s/sims/net/wire/net_wire %s %s %d %d %d' % \
                (env.repodir, connects[0][1],
                        connects[1][1],
                        self.sync_mode, self.sync_period, self.eth_latency)
        if len(env.pcap_file) > 0:
            cmd += ' ' + env.pcap_file
        return cmd

class SwitchNet(NetSim):
    sync = True

    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        cmd = env.repodir + '/sims/net/switch/net_switch'
        cmd += f' -m {self.sync_mode} -S {self.sync_period} -E {self.eth_latency}'

        if not self.sync:
            cmd += ' -u'

        if len(env.pcap_file) > 0:
            cmd += ' -p ' + env.pcap_file
        for (_,n) in self.connect_sockets(env):
            cmd += ' -s ' + n
        for (_,n) in self.listen_sockets(env):
            cmd += ' -h ' + n
        return cmd

    def sockets_cleanup(self, env):
        # cleanup here will just have listening eth sockets, switch also creates
        # shm regions for each with a "-shm" suffix
        cleanup = []
        for s in super().sockets_cleanup(env):
            cleanup.append(s)
            cleanup.append(s + '-shm')
        return cleanup

class TofinoNet(NetSim):
    tofino_log_path = '/tmp/model.ldjson'
    sync = True

    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        cmd = env.repodir + '/sims/tofino/tofino'
        cmd += f' -m {self.sync_mode} -S {self.sync_period} -E {self.eth_latency} -t {self.tofino_log_path}'
        if not self.sync:
            cmd += ' -u'
        for (_,n) in self.connect_sockets(env):
            cmd += ' -s ' + n
        return cmd

class NS3DumbbellNet(NetSim):
    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        ports = ''
        for (n,s) in self.connect_sockets(env):
            if 'server' in n.name:
                ports += '--CosimPortLeft=' + s + ' '
            else:
                ports += '--CosimPortRight=' + s + ' '

        cmd = env.repodir + '/sims/external/ns-3' + '/cosim-run.sh cosim cosim-dumbbell-example ' + ports + ' ' + self.opt
        print(cmd)

        return cmd

class NS3BridgeNet(NetSim):
    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        ports = ''
        for (_,n) in self.connect_sockets(env):
            ports += '--CosimPort=' + n + ' '

        cmd = env.repodir + '/sims/external/ns-3' + '/cosim-run.sh cosim cosim-bridge-example ' + ports + ' ' + self.opt
        print(cmd)

        return cmd

class NS3SequencerNet(NetSim):
    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        ports = ''
        for (n,s) in self.connect_sockets(env):
            if 'client' in n.name:
                ports += '--ClientPort=' + s + ' '
            elif 'replica' in n.name:
                ports += '--ServerPort=' + s + ' '
            elif 'sequencer' in n.name:
                ports += '--EndhostSequencerPort=' + s + ' '
            else:
                raise Exception('Wrong NIC type')
        cmd = env.repodir + '/sims/external/ns-3' + '/cosim-run.sh sequencer sequencer-single-switch-example ' + ports + ' ' + self.opt
        return cmd


class FEMUDev(PCIDevSim):
    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        cmd = '%s%s %s %s' % \
            (env.repodir, '/sims/external/femu/femu-simbricks',
             env.dev_pci_path(self), env.dev_shm_path(self))
        return cmd


def create_basic_hosts(e, num, name_prefix, net, nic_class, host_class,
        nc_class, app_class, ip_start=1, ip_prefix=24):
    hosts = []
    for i in range(0, num):
        nic = nic_class()
        #nic.name = '%s.%d' % (name_prefix, i)
        nic.set_network(net)

        host = host_class()
        host.name = '%s.%d' % (name_prefix, i)

        node_config = nc_class()
        node_config.prefix = ip_prefix
        ip = ip_start + i
        node_config.ip = '10.0.%d.%d' % (int(ip / 256), ip % 256)
        node_config.app = app_class()
        host.set_config(node_config)

        host.add_nic(nic)
        e.add_nic(nic)
        e.add_host(host)

        hosts.append(host)

    return hosts

def create_multinic_hosts(e, num, name_prefix, net, host_class,
        nc_class, app_class, ip_start=1, ip_prefix=24):
    hosts = []

    mn = I40eMultiNIC()
    mn.name = name_prefix
    e.add_nic(mn)

    for i in range(0, num):
        nic = mn.create_subnic()
        #nic.name = '%s.%d' % (name_prefix, i)
        nic.set_network(net)

        host = host_class()
        host.name = '%s.%d' % (name_prefix, i)

        node_config = nc_class()
        node_config.prefix = ip_prefix
        ip = ip_start + i
        node_config.ip = '10.0.%d.%d' % (int(ip / 256), ip % 256)
        node_config.app = app_class()
        host.set_config(node_config)

        host.add_nic(nic)
        e.add_host(host)

        hosts.append(host)

    return hosts


def create_dctcp_hosts(e, num, name_prefix, net, nic_class, host_class,
        nc_class, app_class, cpu_freq, mtu, ip_start=1):
    hosts = []
    for i in range(0, num):
        nic = nic_class()
        #nic.name = '%s.%d' % (name_prefix, i)
        nic.set_network(net)

        host = host_class()
        host.name = '%s.%d' % (name_prefix, i)
        host.cpu_freq = cpu_freq

        node_config = nc_class()
        node_config.mtu = mtu
        node_config.ip = '192.168.64.%d' % (ip_start + i)
        node_config.app = app_class()
        host.set_config(node_config)

        host.add_nic(nic)
        e.add_nic(nic)
        e.add_host(host)

        hosts.append(host)

    return hosts
