# Copyright 2026 Max Planck Institute for Software Systems, and
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

"""Offline image builds with libguestfs.

virt-customize applies the whole layer list in one invocation without booting a
VM, so an ordinary change costs seconds. Work needing a booted guest wants the
packer backend instead.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import shlex
import shutil
import typing as tp

import typing_extensions as tpe

from simbricks.orchestration.system import disk_images, image_layers
from simbricks.utils import base as utils_base

if tp.TYPE_CHECKING:
    from simbricks.orchestration.instantiation import base as inst_base
    from simbricks.orchestration.system import base as sys_base


def _env() -> list[str]:
    """Force the direct appliance backend unless one is already chosen.

    'direct' needs no libvirt session. env(1) because commands run without a
    shell, inheriting the executor's environment.
    """
    if os.environ.get("LIBGUESTFS_BACKEND"):
        return []
    return ["env", "LIBGUESTFS_BACKEND=direct"]


def _version_key(value: str) -> list[tuple[int, object]]:
    """Sort key comparing digit runs numerically, so -100 beats -99."""
    return [
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", value)
        if part
    ]


def _require(exe: str) -> str:
    """Check a tool is present, so a missing one is reported here and by name.

    These cannot be conda dependencies -- neither libguestfs nor qemu is
    packaged for conda -- so the Python package being installed says nothing
    about them being available.
    """
    if shutil.which(exe) is None:
        raise RuntimeError(
            f"'{exe}' not found: GuestfsImage needs libguestfs-tools and qemu-utils"
            " installed on the runner"
        )
    return exe


# Where in the guest the debug kernel's uncompressed ELF lives.
_VMLINUX_DIR = "/usr/lib/debug/boot"


class GuestfsImage(image_layers.LayeredDiskImage):
    """A layered image built offline with virt-customize."""

    def __init__(self, system: sys_base.System, base: disk_images.DiskImage) -> None:
        super().__init__(system, base)
        self.virt_customize_exec = "virt-customize"
        self.qemu_img_exec = "qemu-img"
        self.virt_ls_exec = "virt-ls"
        self.virt_copy_out_exec = "virt-copy-out"

    def available_formats(self) -> list[str]:
        return ["raw", "qcow2"]

    def toJSON(self) -> dict:
        json_obj = super().toJSON()
        json_obj["virt_customize_exec"] = self.virt_customize_exec
        json_obj["qemu_img_exec"] = self.qemu_img_exec
        json_obj["virt_ls_exec"] = self.virt_ls_exec
        json_obj["virt_copy_out_exec"] = self.virt_copy_out_exec
        return json_obj

    @classmethod
    def fromJSON(cls, system: sys_base.System, json_obj: dict) -> tpe.Self:
        instance = super().fromJSON(system, json_obj)
        instance.virt_customize_exec = utils_base.get_json_attr_top(json_obj, "virt_customize_exec")
        instance.qemu_img_exec = utils_base.get_json_attr_top(json_obj, "qemu_img_exec")
        instance.virt_ls_exec = utils_base.get_json_attr_top(json_obj, "virt_ls_exec")
        instance.virt_copy_out_exec = utils_base.get_json_attr_top(json_obj, "virt_copy_out_exec")
        return instance

    # ---- building ----------------------------------------------------------

    def _scratch_dir(self, inst: inst_base.Instantiation) -> pathlib.Path:
        path = pathlib.Path(inst.env.img_dir(f"build.{self.id()}"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _materialize(
        self,
        inst: inst_base.Instantiation,
        file: disk_images.ConfigFile,
        scratch: pathlib.Path,
        ident: str,
    ) -> pathlib.Path:
        """Write a config file out so virt-customize has something to copy in."""
        # Per-layer subdirectory: the file name has to be the guest's, and two
        # layers may use the same one.
        directory = scratch / ident
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / file.file_name
        with file.IOHandle(inst) as src, open(path, "wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
        return path

    def _layer_args(
        self,
        inst: inst_base.Instantiation,
        layers: list[image_layers.ImageLayer],
        scratch: pathlib.Path,
    ) -> list[str]:
        """Lower the layers to virt-customize arguments.

        virt-customize applies operations in command-line order, so the layer
        list maps onto it directly.
        """
        args: list[str] = []
        for index, layer in enumerate(layers):
            if isinstance(layer, image_layers.RunCommand):
                args += ["--run-command", layer.cmd]
            elif isinstance(layer, image_layers.RunScript):
                path = self._materialize(inst, layer.file, scratch, f"l{index}")
                path.chmod(0o755)
                args += ["--run", path.as_posix()]
            elif isinstance(layer, image_layers.AddFiles):
                dest_dir = layer.dest_dir or "/"
                args += ["--mkdir", dest_dir]
                for file in layer.files:
                    path = self._materialize(inst, file, scratch, f"l{index}")
                    args += ["--copy-in", f"{path.as_posix()}:{dest_dir}"]
                    if layer.mode is not None:
                        dest = pathlib.PurePosixPath(dest_dir, file.file_name)
                        args += ["--chmod", f"{layer.mode:04o}:{dest.as_posix()}"]
            else:
                raise RuntimeError(f"GuestfsImage cannot build layer {type(layer).__name__}")
        return args

    async def build(
        self,
        inst: inst_base.Instantiation,
        format: str,
        base_path: str,
        layers: list[image_layers.ImageLayer],
        out_path: str,
    ) -> None:
        scratch = self._scratch_dir(inst)
        # Under a temporary name, so a crashed build leaves nothing the next
        # run would mistake for a finished image.
        partial = f"{out_path}.partial"
        cmds = [
            shlex.join([_require(self.qemu_img_exec), "convert", "-O", format, base_path, partial])
        ]
        args = self._layer_args(inst, layers, scratch)
        if args:
            cmds.append(
                shlex.join(_env() + [_require(self.virt_customize_exec), "-a", partial] + args)
            )
        await inst.command_executor.exec_prepare_cmds(cmds)
        pathlib.Path(partial).rename(out_path)

    # ---- boot artifacts ----------------------------------------------------

    def _built_image(self, inst: inst_base.Instantiation) -> str:
        for format in self.available_formats():
            path = self.path(inst, format)
            if pathlib.Path(path).is_file():
                return path
        raise RuntimeError(f"{type(self).__name__}-{self.id()}: image has not been built yet")

    async def _kernel_version(self, image: str) -> str:
        """Newest kernel installed in the image, read from /boot."""
        proc = await asyncio.create_subprocess_exec(
            *(_env() + [_require(self.virt_ls_exec), "-a", image, "/boot"]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"could not list /boot in '{image}': {stderr.decode(errors='replace')}"
            )
        versions = sorted(
            (
                line[len("vmlinuz-") :]
                for line in stdout.decode(errors="replace").splitlines()
                if line.startswith("vmlinuz-")
            ),
            key=_version_key,
        )
        if not versions:
            raise RuntimeError(f"no /boot/vmlinuz-* in '{image}'")
        return versions[-1]

    async def boot_artifacts(
        self, inst: inst_base.Instantiation, kinds: list[disk_images.BootArtifact]
    ) -> dict[disk_images.BootArtifact, str]:
        if not kinds:
            return {}
        image = self._built_image(inst)
        out_dir = pathlib.Path(inst.env.img_dir(f"boot.{self.id()}"))
        out_dir.mkdir(parents=True, exist_ok=True)

        async with inst.prepare_lock(out_dir.as_posix()):
            wanted = [k for k in kinds if not (out_dir / k.value).is_file()]
            if wanted:
                version = await self._kernel_version(image)
                # What each kind is called inside the guest.
                guest_names = {
                    disk_images.BootArtifact.VMLINUZ: f"/boot/vmlinuz-{version}",
                    disk_images.BootArtifact.INITRD: f"/boot/initrd.img-{version}",
                    disk_images.BootArtifact.VMLINUX: f"{_VMLINUX_DIR}/vmlinux-{version}",
                }
                # One copy-out for all: the appliance boot is the cost, not the
                # number of files.
                cmd = _env() + [_require(self.virt_copy_out_exec), "-a", image]
                cmd += [guest_names[k] for k in wanted]
                cmd += [out_dir.as_posix()]
                await inst.command_executor.exec_prepare_cmds([shlex.join(cmd)])
                for kind in wanted:
                    src = out_dir / pathlib.PurePosixPath(guest_names[kind]).name
                    if not src.is_file():
                        msg = f"'{guest_names[kind]}' is not in this image."
                        if kind is disk_images.BootArtifact.VMLINUX:
                            msg += (
                                " An uncompressed vmlinux comes from the kernel's debug"
                                " package; add a layer that installs it."
                            )
                        raise RuntimeError(msg)
                    src.rename(out_dir / kind.value)

        return {k: (out_dir / k.value).as_posix() for k in kinds}
