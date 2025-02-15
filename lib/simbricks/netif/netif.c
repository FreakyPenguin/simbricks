/*
 * Copyright 2021 Max Planck Institute for Software Systems, and
 * National University of Singapore
 *
 * Permission is hereby granted, free of charge, to any person obtaining
 * a copy of this software and associated documentation files (the
 * "Software"), to deal in the Software without restriction, including
 * without limitation the rights to use, copy, modify, merge, publish,
 * distribute, sublicense, and/or sell copies of the Software, and to
 * permit persons to whom the Software is furnished to do so, subject to
 * the following conditions:
 *
 * The above copyright notice and this permission notice shall be
 * included in all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
 * EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
 * MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
 * IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
 * CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
 * TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
 * SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 */

#include "lib/simbricks/netif/netif.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>

#include <simbricks/proto/base.h>

#include "lib/simbricks/netif/internal.h"

static uint64_t current_epoch = 0;

int SimbricksNetIfInit(struct SimbricksNetIf *nsif, const char *eth_socket_path,
                       int *sync_eth) {
  struct SimbricksProtoNetDevIntro di;
  struct SimbricksProtoNetNetIntro ni;
  int cfd, shm_fd;
  void *p;

  if ((cfd = uxsocket_connect(eth_socket_path)) < 0) {
    return -1;
  }

  memset(&ni, 0, sizeof(ni));

  if (*sync_eth)
    ni.flags |= SIMBRICKS_PROTO_NET_FLAGS_NI_SYNC;

  if (send(cfd, &ni, sizeof(ni), 0) != sizeof(ni)) {
    perror("sending net intro failed");
    return -1;
  }

  if (uxsocket_recv(cfd, &di, sizeof(di), &shm_fd)) {
    return -1;
  }

  if ((p = shm_map(shm_fd)) == NULL) {
    return -1;
  }
  close(shm_fd);

  if ((di.flags & SIMBRICKS_PROTO_NET_FLAGS_DI_SYNC) == 0) {
    *sync_eth = 0;
    nsif->sync = 0;
  } else {
    nsif->sync = *sync_eth;
  }

  nsif->d2n_queue = (uint8_t *)p + di.d2n_offset;
  nsif->n2d_queue = (uint8_t *)p + di.n2d_offset;
  nsif->d2n_elen = di.d2n_elen;
  nsif->n2d_elen = di.n2d_elen;
  nsif->d2n_enum = di.d2n_nentries;
  nsif->n2d_enum = di.n2d_nentries;
  nsif->d2n_pos = nsif->n2d_pos = 0;
  nsif->d2n_timestamp = nsif->n2d_timestamp = 0;

  return 0;
}

void SimbricksNetIfCleanup(struct SimbricksNetIf *nsif) {
  fprintf(stderr, "SimbricksNetIfCleanup: TODO\n");
  abort();
}

volatile union SimbricksProtoNetD2N *SimbricksNetIfD2NPoll(
    struct SimbricksNetIf *nsif, uint64_t timestamp) {
  volatile union SimbricksProtoNetD2N *msg =
      (volatile union SimbricksProtoNetD2N *)(nsif->d2n_queue +
                                              nsif->d2n_pos * nsif->d2n_elen);

  /* message not ready */
  if ((msg->dummy.own_type & SIMBRICKS_PROTO_NET_D2N_OWN_MASK) !=
      SIMBRICKS_PROTO_NET_D2N_OWN_NET)
    return NULL;

  /* if in sync mode, wait till message is ready */
  nsif->d2n_timestamp = msg->dummy.timestamp;
  if (nsif->sync && nsif->d2n_timestamp > timestamp)
    return NULL;

  nsif->d2n_pos = (nsif->d2n_pos + 1) % nsif->d2n_enum;
  return msg;
}

void SimbricksNetIfD2NDone(struct SimbricksNetIf *nsif,
                           volatile union SimbricksProtoNetD2N *msg) {
  msg->dummy.own_type =
      (msg->dummy.own_type & SIMBRICKS_PROTO_NET_D2N_MSG_MASK) |
      SIMBRICKS_PROTO_NET_D2N_OWN_DEV;
}

volatile union SimbricksProtoNetN2D *SimbricksNetIfN2DAlloc(
    struct SimbricksNetIf *nsif, uint64_t timestamp, uint64_t latency) {
  volatile union SimbricksProtoNetN2D *msg =
      (volatile union SimbricksProtoNetN2D *)(nsif->n2d_queue +
                                              nsif->n2d_pos * nsif->n2d_elen);

  if ((msg->dummy.own_type & SIMBRICKS_PROTO_NET_N2D_OWN_MASK) !=
      SIMBRICKS_PROTO_NET_N2D_OWN_NET) {
    return NULL;
  }

  msg->dummy.timestamp = timestamp + latency;
  nsif->n2d_timestamp = timestamp;

  nsif->n2d_pos = (nsif->n2d_pos + 1) % nsif->n2d_enum;
  return msg;
}

int SimbricksNetIfN2DSync(struct SimbricksNetIf *nsif, uint64_t timestamp,
                          uint64_t latency, uint64_t sync_delay,
                          int sync_mode) {
  volatile union SimbricksProtoNetN2D *msg;
  volatile struct SimbricksProtoNetN2DSync *sync;
  int do_sync;

  if (!nsif->sync)
    return 0;

  switch (sync_mode) {
    case SIMBRICKS_PROTO_SYNC_SIMBRICKS:
      do_sync = nsif->n2d_timestamp == 0 ||
                timestamp - nsif->n2d_timestamp >= sync_delay;
      break;
    case SIMBRICKS_PROTO_SYNC_BARRIER:
      do_sync = current_epoch == 0 || timestamp - current_epoch >= sync_delay;
      break;
    default:
      fprintf(stderr, "unsupported sync mode=%u\n", sync_mode);
      return 0;
  }

  if (!do_sync) {
    return 0;
  }

  msg = SimbricksNetIfN2DAlloc(nsif, timestamp, latency);
  if (msg == NULL)
    return -1;

  sync = &msg->sync;
  // WMB();
  sync->own_type =
      SIMBRICKS_PROTO_NET_N2D_MSG_SYNC | SIMBRICKS_PROTO_NET_N2D_OWN_DEV;

  return 0;
}

void SimbricksNetIfAdvanceEpoch(uint64_t timestamp, uint64_t sync_delay,
                                int sync_mode) {
  if (sync_mode == SIMBRICKS_PROTO_SYNC_BARRIER) {
    if (timestamp - current_epoch >= sync_delay) {
      current_epoch = timestamp;
    }
  }
}

uint64_t SimbricksNetIfAdvanceTime(uint64_t timestamp, uint64_t sync_delay,
                                   int sync_mode) {
  switch (sync_mode) {
    case SIMBRICKS_PROTO_SYNC_SIMBRICKS:
      return timestamp;
    case SIMBRICKS_PROTO_SYNC_BARRIER:
      return timestamp < current_epoch + sync_delay
                 ? timestamp
                 : current_epoch + sync_delay;
    default:
      fprintf(stderr, "unsupported sync mode=%u\n", sync_mode);
      return timestamp;
  }
}
