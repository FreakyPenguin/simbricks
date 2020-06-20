REPO_BASE= $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

all: \
	corundum/corundum_verilator \
	corundum_bm/corundum_bm \
	net_tap/net_tap \
	net_wire/net_wire

clean:
	$(MAKE) -C corundum/ clean
	$(MAKE) -C corundum_bm/ clean
	$(MAKE) -C dummy_nic/ clean
	$(MAKE) -C net_tap/ clean
	$(MAKE) -C net_wire/ clean
	$(MAKE) -C nicsim_common/ clean
	$(MAKE) -C netsim_common/ clean


####################################
# Tools in this repo

corundum/corundum_verilator: nicsim_common/libnicsim_common.a
	$(MAKE) -C corundum/ all

corundum_bm/corundum_bm: nicsim_common/libnicsim_common.a
	$(MAKE) -C corundum_bm/ all

dummy_nic/dummy_nic: nicsim_common/libnicsim_common.a
	$(MAKE) -C dummy_nic all

net_tap/net_tap: netsim_common/libnetsim_common.a
	$(MAKE) -C net_tap/

net_wire/net_wire: netsim_common/libnetsim_common.a
	$(MAKE) -C net_wire/

nicsim_common/libnicsim_common.a:
	$(MAKE) -C nicsim_common/

netsim_common/libnetsim_common.a:
	$(MAKE) -C netsim_common/


####################################
# External dependencies

external: gem5/ready qemu/ready ns-3/ready

gem5:
	git clone git@github.com:nicklijl/gem5.git gem5

gem5/ready: gem5
	+cd gem5 && scons build/X86/gem5.opt -j`nproc`
	touch gem5/ready

qemu:
	git clone git@github.com:FreakyPenguin/qemu-cosim.git qemu

qemu/ready: qemu
	cd qemu && ./configure \
	    --target-list=x86_64-softmmu \
	    --disable-werror \
	    --extra-cflags="-I$(REPO_BASE)/proto" \
	    --enable-cosim-pci && \
	  $(MAKE)
	touch qemu/ready

ns-3:
	git clone git@github.com:FreakyPenguin/ns-3-cosim.git ns-3

ns-3/ready: ns-3 netsim_common/libnetsim_common.a
	+cd ns-3 && COSIM_PATH=$(REPO_BASE) ./cosim-build.sh configure
	touch ns-3/ready

####################################
# External dependencies

run-experiments: all build-images external
	$(MAKE) -C experiments

build-images:
	$(MAKE) -C images
