#include "vfio.h"
#include <stdio.h>
#include <stdlib.h>


int main()
{
  struct vfio_dev dev;
  void *regs;
  size_t reg_len;

  // todo: should eventually look up by vendor:device
  if (vfio_dev_open(&dev, "/dev/vfio/noiommu-0", "0000:00:02.0") != 0) {
    fprintf(stderr, "open device failed\n");
    return -1;
  }

  if(vfio_region_map(&dev, 0, &regs, &reg_len)) {
    fprintf(stderr, "mapping registers failed\n");
    return -1;
  }
  fprintf(stderr, "register regs=%p len=%zu\n", regs, reg_len);

  fprintf(stderr, "Read from register @0x0...\n");
  fprintf(stderr, "    read=val=%lx\n", READ_REG64(regs, 0));
  fprintf(stderr, "Writing 0x42 to register @0x20...\n");
  WRITE_REG64(regs, 0x20, 0x42);
  fprintf(stderr, "Done\n");


  return 0;
}
