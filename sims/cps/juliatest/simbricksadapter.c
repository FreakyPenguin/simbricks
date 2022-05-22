#include <stdbool.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <simbricks/pcie/if.h>

static struct SimbricksBaseIfSHMPool pool;
static struct SimbricksPcieIf pcieif;

static void abort_iffail(bool success, const char *msg)
{
  if (success)
    return;
  fprintf(stderr, "failed: %s\n", msg);
  abort();
}

void simbricks_adapter_init(const char *socket_path,
                            const char *shm_path,
                            bool enable_sync)
{
  /** fill in essential PCI device info */
  struct SimbricksProtoPcieDevIntro di;
  memset(&di, 0, sizeof(di));
  di.bars[0].len = 0x1000;
  di.bars[0].flags = SIMBRICKS_PROTO_PCIE_BAR_64;
  di.pci_vendor_id = 0x9876;
  di.pci_device_id = 0x1234;

  struct SimbricksBaseIfParams pcieParams;
  // sets defaults for everything inculding a link latency of 500ns
  SimbricksPcieIfDefaultParams(&pcieParams);

  pcieParams.blocking_conn = true;
  pcieParams.sock_path = socket_path;
  pcieParams.sync_mode = (enable_sync ?
      kSimbricksBaseIfSyncRequired : kSimbricksBaseIfSyncDisabled);

  abort_iffail(!SimbricksBaseIfSHMPoolCreate(&pool, shm_path,
        SimbricksBaseIfSHMSize(&pcieParams)),
      "pool create");

  struct SimbricksBaseIf *bif = &pcieif.base;
  abort_iffail(!SimbricksBaseIfInit(bif, &pcieParams), "base init");
  abort_iffail(!SimbricksBaseIfListen(bif, &pool), "listen");
  abort_iffail(!SimbricksBaseIfIntroSend(bif, &di, sizeof(di)), "intro send");
  struct SimbricksProtoPcieHostIntro hi;
  size_t hl = sizeof(hi);
  abort_iffail(!SimbricksBaseIfIntroRecv(bif, &hi, &hl), "intro recv");
}

void *simbricks_adapter_getevent(uint64_t ts)
{
  while (SimbricksPcieIfD2HOutSync(&pcieif, ts)) {
    fprintf(stderr, "warning: sync failed\n");
  }

  return (void *) SimbricksPcieIfH2DInPoll(&pcieif, ts);
}

bool simbricks_adapter_getread(void *ev, uint64_t *id, uint64_t *addr,
                               uint8_t *len)
{
  volatile union SimbricksProtoPcieH2D *msg =
    (volatile union SimbricksProtoPcieH2D *) ev;

  if (!msg || SimbricksPcieIfH2DInType(&pcieif, msg) !=
      SIMBRICKS_PROTO_PCIE_H2D_MSG_READ)
    return false;

  *id = msg->read.req_id;
  *addr = msg->read.offset;
  *len = msg->read.len;
  return true;
}

bool simbricks_adapter_getwrite(void *ev, uint64_t *id, uint64_t *addr,
                                uint8_t *len, uint64_t *val)
{
  volatile union SimbricksProtoPcieH2D *msg =
    (volatile union SimbricksProtoPcieH2D *) ev;

  if (!msg || SimbricksPcieIfH2DInType(&pcieif, msg) !=
      SIMBRICKS_PROTO_PCIE_H2D_MSG_WRITE)
    return false;

  *id = msg->write.req_id;
  *addr = msg->write.offset;
  *len = msg->write.len;

  *val = 0;
  memcpy(val, (void *) msg->write.data, *len);
  return true;
}

void simbricks_adapter_eventdone(void *ev)
{
  volatile union SimbricksProtoPcieH2D *msg =
    (volatile union SimbricksProtoPcieH2D *) ev;
  if (!msg)
    return;
  SimbricksPcieIfH2DInDone(&pcieif, msg);
}

uint64_t simbricks_adapter_nextts(void)
{
  if (!SimbricksBaseIfSyncEnabled(&pcieif.base))
    return 0;

  /* next thing due might be an incoming event or a sync we have to send */
  uint64_t rx_next = SimbricksPcieIfH2DInTimestamp(&pcieif);
  uint64_t sync_next = SimbricksPcieIfD2HOutNextSync(&pcieif);
  return (rx_next <= sync_next ? rx_next : sync_next);
}

static inline volatile union SimbricksProtoPcieD2H *alloc_out(uint64_t ts)
{
  volatile union SimbricksProtoPcieD2H *msg;

  bool first = true;
  while ((msg = SimbricksPcieIfD2HOutAlloc(&pcieif, ts)) == NULL) {
    if (first) {
      fprintf(stderr, "waiting for SimbricksPcieIfD2HOutAlloc\n");
      first = false;
    }
  }

  if (!first)
    fprintf(stderr, "SimbricksPcieIfD2HOutAlloc succeeded\n");

  return msg;
}

void simbricks_adapter_complr(uint64_t id, uint64_t val, uint8_t len, uint64_t ts)
{
  volatile union SimbricksProtoPcieD2H *msg = alloc_out(ts);
  memcpy((void *) msg->readcomp.data, &val, len);
  msg->readcomp.req_id = id;
  SimbricksPcieIfD2HOutSend(&pcieif, msg,
    SIMBRICKS_PROTO_PCIE_D2H_MSG_READCOMP);
}

void simbricks_adapter_complw(uint64_t id, uint64_t ts)
{
  volatile union SimbricksProtoPcieD2H *msg = alloc_out(ts);
  msg->writecomp.req_id = id;
  SimbricksPcieIfD2HOutSend(&pcieif, msg,
    SIMBRICKS_PROTO_PCIE_D2H_MSG_WRITECOMP);

}

