import simbricks.experiments as exp
import simbricks.simulators as sim
import simbricks.nodeconfig as node


# This basically just configures linux with the basic vfio-pci driver for the
# cps_impl app to be able to access the device. (we keep these steps minimal by
# default to cut boot time)
class CPSNode(node.NodeConfig):
    def __init__(self):
        self.drivers = []

    def prepare_pre_cp(self):
        l = []
        l.append('mount -t proc proc /proc')
        l.append('mount -t sysfs sysfs /sys')
        l.append('echo 1 > /sys/module/vfio/parameters/enable_unsafe_noiommu_mode')
        #l.append('modprobe vfio enable_unsafe_noiommu_mode=1')
        #l.append('modprobe vfio-pci')
        return super().prepare_post_cp() + l

    def prepare_post_cp(self):
        l = []
        # might be able to do this pre-cp as well...
        #l.append("echo 0000:` lspci -n | grep '9876:1234' | cut -d ' ' -f 1 ` >/sys/bus/pci/drivers/vfio-pci/bind")
        l.append('ls -a /dev/vfio')
        l.append('ls /sys/kernel/iommu_groups/')
        l.append('cat /sys/bus/pci/devices/0000:00:02.0/vendor')
        l.append('echo 9876 1234 >/sys/bus/pci/drivers/vfio-pci/new_id')
        return super().prepare_post_cp() + l


class CPSApp(node.AppConfig):
    def run_cmds(self, node):
        # application command to run once the host is booted up
        return ['/tmp/guest/cps_impl']

    def config_files(self):
        # copy cps impl binary into host image during prep
        m = {'cps_impl': open('cps_impl', 'rb')}
        return {**m, **super().config_files()}


class JuliatestSim(sim.PCIDevSim):
    sync = True

    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        cmd = 'julia %s%s %s %s %s' % \
            (env.repodir, '/sims/cps/juliatest/juliatest.jl',
             env.dev_pci_path(self), env.dev_shm_path(self),
             'y' if self.sync else 'n')
        return cmd

class PythontestSim(sim.PCIDevSim):
    sync = True

    def __init__(self):
        super().__init__()

    def run_cmd(self, env):
        cmd = 'python3 %s%s %s %s %s' % \
            (env.repodir, '/sims/cps/juliatest/pythontest.py',
             env.dev_pci_path(self), env.dev_shm_path(self),
             'y' if self.sync else 'n')
        return cmd


# Set this to true to enable synchronization with qemu + instruction counting
synchronized = False

e = exp.Experiment('juliatest')

server = sim.QemuHost()
server.name = 'host'
server.sync = synchronized
server_config = CPSNode()
server.set_config(server_config)
server_config.app = CPSApp()
server.wait = True

cpssim = JuliatestSim()
cpssim.name = 'cps'
cpssim.sync = synchronized
server.add_pcidev(cpssim)

e.add_pcidev(cpssim)
e.add_host(server)

experiments = [e]
