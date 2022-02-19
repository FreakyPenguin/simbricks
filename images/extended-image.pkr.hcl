
variable "cpus" {
  type    = string
  default = "4"
}

variable "memory" {
  type    = string
  default = "4096"
}

variable "outname" {
  type    = string
  default = "extended"
}

variable "base_img" {
  type    = string
  default = "base"
}

source "qemu" "autogenerated_1" {
  communicator     = "ssh"
  cpus             = "${var.cpus}"
  disk_image       = true
  headless         = true
  iso_checksum     = "none"
  iso_url          = "output-${var.base_img}/${var.base_img}"
  memory           = "${var.memory}"
  net_device       = "virtio-net"
  output_directory = "output-${var.outname}"
  qemuargs         = [["-machine", "pc-q35-4.2,accel=kvm:tcg,usb=off,vmport=off,dump-guest-core=off"],
                      ["-cpu", "host"]]
  shutdown_command = "sudo shutdown --poweroff --no-wall now"
  ssh_password     = "ubuntu"
  ssh_username     = "ubuntu"
  use_backing_file = "true"
  vm_name          = "${var.outname}"
}

build {
  sources = ["source.qemu.autogenerated_1"]

  provisioner "file" {
    direction = "upload"
    source = "input-${var.outname}"
    destination = "/tmp/input"
  }

  provisioner "shell" {
    execute_command = "{{ .Vars }} sudo -S -E bash '{{ .Path }}'"
    scripts         = ["scripts/install-${var.outname}.sh", "scripts/cleanup.sh"]
  }

}
