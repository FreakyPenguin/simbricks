#!/bin/sh

cd /simbricks
mkdir -p sims/external/qemu-keep/build/x86_64-softmmu
mv sims/external/qemu/build/qemu-img sims/external/qemu-keep/build/
mv sims/external/qemu/build/qemu-system-x86_64 sims/external/qemu-keep/build/
mv sims/external/qemu/build/x86_64-softmmu/qemu-system-x86_64 \
    sims/external/qemu-keep/build/x86_64-softmmu/
rm -rf sims/external/qemu
mv sims/external/qemu-keep sims/external/qemu

mkdir -p sims/external/gem5-keep/build/X86
mv sims/external/gem5/build/X86/gem5.opt sims/external/gem5-keep/build/X86/
mv sims/external/gem5/configs sims/external/gem5-keep/
rm -rf sims/external/gem5
mv sims/external/gem5-keep sims/external/gem5

for d in sequencer cosim
do
  mkdir -p sims/external/ns-3-keep/build/src/$d/examples
  mv sims/external/ns-3/build/src/$d/examples/ns3-dev-* \
      sims/external/ns-3-keep/build/src/$d/examples/
done
rm -rf sims/external/ns-3
mv sims/external/ns-3-keep sims/external/ns-3

rm -rf `find -name .git`
