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

#include "dist/net_rdma.h"

#include <fcntl.h>
#include <rdma/rdma_cma.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/epoll.h>
#include <unistd.h>

#define MSG_RXBUFS 16
#define MSG_TXBUFS 16

struct NetRdmaMsg {
  union {
    struct SimbricksProtoNetDevIntro dev;
    struct SimbricksProtoNetNetIntro net;
    struct NetRdmaMsg *next_free;
  };
  uint64_t id;
  uint64_t base_addr;
  uint64_t queue_off;
  uint64_t rkey;
  enum {
    kMsgDev,
    kMsgNet,
  } msg_type;
} __attribute__ ((packed));

static struct rdma_event_channel *cm_channel;
static struct rdma_conn_param conn_param = { };
static struct rdma_cm_id *cm_id;
static struct ibv_pd *pd;
static struct ibv_cq *cq;
static struct ibv_comp_channel *comp_chan;
static struct ibv_mr *mr_shm;
static struct ibv_mr *mr_msgs;
static struct ibv_qp_init_attr qp_attr = { };

static struct NetRdmaMsg msgs[MSG_RXBUFS + MSG_TXBUFS];
static struct NetRdmaMsg *msgs_free = NULL;

static struct NetRdmaMsg *RdmaMsgAlloc() {
  struct NetRdmaMsg *msg = msgs_free;
  if (msg != NULL) {
    msgs_free = msg->next_free;
  }
  return msg;
}

static void RdmaMsgFree(struct NetRdmaMsg *msg) {
  msg->next_free = msgs_free;
  msgs_free = msg;
}

static int RdmMsgRxEnqueue(struct NetRdmaMsg *msg) {
  struct ibv_sge sge = { };
  sge.addr = (uintptr_t) msg;
  sge.length = sizeof(*msg);
  sge.lkey = mr_msgs->lkey;

  struct ibv_recv_wr recv_wr = { };
  recv_wr.wr_id = msg - msgs;
  recv_wr.sg_list = &sge;
  recv_wr.num_sge = 1;
  struct ibv_recv_wr *bad_recv_wr;
  if (ibv_post_recv(cm_id->qp, &recv_wr, &bad_recv_wr)) {
    perror("RdmMsgRxEnqueue: ibv_post_recv failed");
    return 1;
  }

  return 0;
}

static int RdmaMsgRx(struct NetRdmaMsg *msg) {
  if (msg->id >= peer_num) {
    fprintf(stderr, "RdmMsgRx: invalid peer id in message (%lu)\n", msg->id);
    abort();
  }

  struct Peer *peer = peers + msg->id;
  printf("RdmMsgRx -> peer %s\n", peer->sock_path);

  if (peer->is_dev != (msg->msg_type == kMsgNet)) {
    fprintf(stderr, "RdmMsgRx: unexpetced message type (%u)\n", msg->msg_type);
    abort();
  }

  if (peer->intro_valid_remote) {
    fprintf(stderr, "RdmMsgRx: received multiple messages (%lu)\n", msg->id);
    abort();
  }

  peer->remote_rkey = msg->rkey;
  peer->remote_base = msg->base_addr + msg->queue_off;
  peer->intro_valid_remote = true;
  if (peer->is_dev) {
    peer->net_intro = msg->net;
    if (PeerDevSendIntro(peer))
      return 1;
  } else {
    peer->dev_intro = msg->dev;
    if (PeerNetSetupQueues(peer))
      return 1;
    if (peer->intro_valid_local && RdmaPassIntro(peer))
      return 1;
  }

  if (peer->intro_valid_local) {
    fprintf(stderr, "RdmMsgRx(%s): marking peer as ready\n", peer->sock_path);
    peer->ready = true;
  }
  return 0;
}

static int RdmaCommonInit() {
  if (!(pd = ibv_alloc_pd(cm_id->verbs))) {
    perror("RdmaCommonInit: ibv_alloc_pd failed");
    return 1;
  }

  if (!(comp_chan = ibv_create_comp_channel(cm_id->verbs))) {
    perror("RdmaCommonInit: ibv_create_comp_channel failed");
    return 1;
  }

  if (!(cq = ibv_create_cq(cm_id->verbs, 1024, NULL, comp_chan, 0))) {
    perror("RdmaCommonInit: ibv_create_cq failed");
    return 1;
  }

  if (!(mr_shm = ibv_reg_mr(pd, shm_base, shm_size,
                            IBV_ACCESS_LOCAL_WRITE |
                            IBV_ACCESS_REMOTE_WRITE))) {
    perror("RdmaCommonInit: ibv_reg_mr shm failed");
    return 1;
  }
  if (!(mr_msgs = ibv_reg_mr(pd, msgs, sizeof(msgs),
                            IBV_ACCESS_LOCAL_WRITE))) {
    perror("RdmaCommonInit: ibv_reg_mr msgs failed");
    return 1;
  }

  qp_attr.cap.max_send_wr = MSG_TXBUFS;
  qp_attr.cap.max_send_sge = 1;
  qp_attr.cap.max_recv_wr = MSG_RXBUFS;
  qp_attr.cap.max_recv_sge = 1;
  qp_attr.send_cq = cq;
  qp_attr.recv_cq = cq;
  qp_attr.qp_type = IBV_QPT_RC;
  qp_attr.sq_sig_all = 1;

  if (rdma_create_qp(cm_id, pd, &qp_attr)) {
    perror("RdmaCommonInit: rdma_create_qp failed");
    return 1;
  }

  if (ibv_req_notify_cq(cq, 0)) {
    perror("RdmMsgRxEnqueue: ibv_req_notify_cq failed");
    return 1;
  }
  fprintf(stderr, "Enqueue rx buffers\n");
  // post receive operations for all rx buffers
  for (int i = 0; i < MSG_RXBUFS; i++)
    if (RdmMsgRxEnqueue(&msgs[i]))
      return 1;

  // add tx buffers to freelist
  for (int i = 0; i < MSG_TXBUFS; i++)
    RdmaMsgFree(&msgs[MSG_RXBUFS + i]);

  return 0;
}

static int RdmaCommonSetNonblock() {
  int flags = fcntl(comp_chan->fd, F_GETFL);
  if (fcntl(comp_chan->fd, F_SETFL, flags | O_NONBLOCK)) {
    perror("RdmaCommonSetNonblock: fcntl set nonblock failed");
    return 1;
  }

  struct epoll_event epev;
  epev.events = EPOLLIN;
  epev.data.ptr = NULL;
  if (epoll_ctl(epfd, EPOLL_CTL_ADD, comp_chan->fd, &epev)) {
    perror("RdmaCommonSetNonblock: epoll_ctl failed");
    return 1;
  }

  return 0;
}

int RdmaListen(struct sockaddr_in *addr) {
  if (!(cm_channel = rdma_create_event_channel())) {
    perror("RdmaListen: rdma_create_event_channel failed");
    return 1;
  }

  struct rdma_cm_id *listen_id;
  if (rdma_create_id(cm_channel, &listen_id, NULL, RDMA_PS_TCP)) {
    perror("RdmaListen: rdma_create_id failed");
    return 1;
  }

  if (rdma_bind_addr(listen_id, (struct sockaddr *) addr)) {
    perror("RdmaListen: rdma_bind_addr failed");
    return 1;
  }

  if (rdma_listen(listen_id, 1)) {
    perror("RdmaListen: rdma_listen failed");
    return 1;
  }

  fprintf(stderr, "RdmaListen: listen done\n");
  struct rdma_cm_event *event;
  if (rdma_get_cm_event(cm_channel, &event)) {
    perror("RdmaListen: rdma_get_cm_event failed");
    return 1;
  }
  if (event->event != RDMA_CM_EVENT_CONNECT_REQUEST) {
    fprintf(stderr, "RdmaListen: unexpected event (%u)\n", event->event);
    return 1;
  }
  cm_id = event->id;
  rdma_ack_cm_event(event);

  fprintf(stderr, "RdmaListen: got conn request\n");

  if (RdmaCommonInit())
    return 1;

  conn_param.responder_resources = 1;
  if (rdma_accept(cm_id, &conn_param)) {
    perror("RdmaListen: rdma_accept failed");
    return 1;
  }

  fprintf(stderr, "RdmaListen: accept done\n");
  if (rdma_get_cm_event(cm_channel, &event)) {
    perror("RdmaListen: rdma_get_cm_event failed");
    return 1;
  }
  if (event->event != RDMA_CM_EVENT_ESTABLISHED) {
    fprintf(stderr, "RdmaListen: unexpected event (%u)\n", event->event);
    return 1;
  }
  rdma_ack_cm_event(event);
  fprintf(stderr, "RdmaListen: conn established\n");

  if (RdmaCommonSetNonblock())
    return 1;

  return 0;
}

int RdmaConnect(struct sockaddr_in *addr) {
  if (!(cm_channel = rdma_create_event_channel())) {
    perror("RdmaConnect: rdma_create_event_channel failed");
    return 1;
  }

  if (rdma_create_id(cm_channel, &cm_id, NULL, RDMA_PS_TCP)) {
    perror("RdmaConnect: rdma_create_id failed");
    return 1;
  }

  if (rdma_resolve_addr(cm_id, NULL, (struct sockaddr *) addr, 5000)) {
    perror("RdmaConnect: rdma_resolve_addr failed");
    return 1;
  }
  struct rdma_cm_event *event;
  if (rdma_get_cm_event(cm_channel, &event)) {
    perror("RdmaConnect: rdma_get_cm_event failed (addr)");
    return 1;
  }
  if (event->event != RDMA_CM_EVENT_ADDR_RESOLVED) {
    fprintf(stderr, "RdmaConnect: unexpected event (%u instead of %u)\n",
            event->event, RDMA_CM_EVENT_ADDR_RESOLVED);
    return 1;
  }
  rdma_ack_cm_event(event);
  fprintf(stderr, "RdmaConnect: address resolved\n");

  if (rdma_resolve_route(cm_id, 5000)) {
    perror("RdmaConnect: rdma_resolve_route failed");
    return 1;
  }
  if (rdma_get_cm_event(cm_channel, &event)) {
    perror("RdmaConnect: rdma_get_cm_event failed (route)");
    return 1;
  }
  if (event->event != RDMA_CM_EVENT_ROUTE_RESOLVED) {
    fprintf(stderr, "RdmaConnect: unexpected event (%u instead of %u)\n",
            event->event, RDMA_CM_EVENT_ROUTE_RESOLVED);
    return 1;
  }
  rdma_ack_cm_event(event);
  fprintf(stderr, "RdmaConnect: route resolved\n");

  if (RdmaCommonInit())
    return 1;

  conn_param.initiator_depth = 1;
  conn_param.retry_count     = 7;
  if (rdma_connect(cm_id, &conn_param)) {
    perror("RdmaConnect: rdma_connect failed");
    return 1;
  }

  fprintf(stderr, "RdmaConnect: connect issued\n");
  if (rdma_get_cm_event(cm_channel, &event)) {
    perror("RdmaConnect: rdma_get_cm_event failed (connect)");
    return 1;
  }
  if (event->event != RDMA_CM_EVENT_ESTABLISHED) {
    fprintf(stderr, "RdmaConnect: unexpected event (%u)\n", event->event);
    return 1;
  }
  rdma_ack_cm_event(event);

  if (RdmaCommonSetNonblock())
    return 1;

  return 0;
}

int RdmaEvent() {
  fprintf(stderr, "RdmaEvent [pid=%d]\n", getpid());

  struct ibv_cq *ecq;
  void *ectx;
  if (ibv_get_cq_event(comp_chan, &ecq, &ectx)) {
    perror("RdmaEvent: ibv_get_cq_event failed");
    return 1;
  }
  ibv_ack_cq_events(ecq, 1);

  if (ibv_req_notify_cq(cq, 0)) {
    perror("RdmaEvent: ibv_req_notify_cq failed");
    return 1;
  }

  int n;
  do {
    const size_t kNumWC = 8;
    struct ibv_wc wcs[kNumWC];
    if ((n = ibv_poll_cq(cq, kNumWC, wcs)) < 0) {
      perror("RdmaEvent: ibv_poll_cq failed");
      return 1;
    }
    fprintf(stderr, "  n=%d\n", n);
    for (int i = 0; i < n; i++) {
      if (wcs[i].opcode == IBV_WC_SEND) {
        fprintf(stderr, "Send done\n", n);
        if (wcs[i].status != IBV_WC_SUCCESS) {
          fprintf(stderr, "RdmaEvent: unsuccessful send (%u)\n", wcs[i].status);
          abort();
        }

        // need to free the send buffer again
        RdmaMsgFree(msgs + wcs[i].wr_id);
      } else if ((wcs[i].opcode & IBV_WC_RECV)) {
        fprintf(stderr, "Recv done\n", n);

        if (wcs[i].status != IBV_WC_SUCCESS) {
          fprintf(stderr, "RdmaEvent: unsuccessful recv (%u)\n", wcs[i].status);
          abort();
        }
        struct NetRdmaMsg *msg = msgs + wcs[i].wr_id;
        if (RdmaMsgRx(msg) || RdmMsgRxEnqueue(msg))
          return 1;
      } else {
        fprintf(stderr, "RdmaEvent: unexpected opcode %u\n", wcs[i].opcode);
        abort();
      }
    }
  } while (n > 0);

  fflush(stdout);
  return 0;
}

int RdmaPassIntro(struct Peer *peer) {
  fprintf(stderr, "RdmaPassIntro(%s)\n", peer->sock_path);

  // device peers have sent us an SHM region, need to register this an as MR
  if (peer->is_dev) {
    if (!(peer->shm_mr = ibv_reg_mr(pd, peer->shm_base, peer->shm_size,
                                    IBV_ACCESS_LOCAL_WRITE |
                                    IBV_ACCESS_REMOTE_WRITE))) {
      perror("RdmaPassIntro: ibv_reg_mr shm failed");
      return 1;
    }
  } else {
    /* on the network side we need to make sure we have received the device
       intro from our RDMA peer, so we can include the queue position. */
    if (!peer->intro_valid_remote) {
      fprintf(stderr,
              "RdmaPassIntro: skipping because remote intro not received\n");
      return 0;
    }

    peer->shm_mr = mr_shm;
    peer->shm_base = shm_base;
    peer->shm_size = shm_size;
  }

  struct NetRdmaMsg *msg = RdmaMsgAlloc();
  if (!msg)
    return 1;

  msg->id = peer - peers;
  msg->base_addr = (uintptr_t) peer->shm_base;
  msg->rkey = peer->shm_mr->rkey;
  if (peer->is_dev) {
    msg->msg_type = kMsgDev;
    /* this is a device peer, meaning the remote side will write to the
       network-to-device queue. */
    msg->queue_off = peer->dev_intro.n2d_offset;
    msg->dev = peer->dev_intro;
  } else {
    msg->msg_type = kMsgNet;
    /* this is a network peer, meaning the remote side will write to the
       device-to-network queue. */
    msg->queue_off = peer->dev_intro.d2n_offset;
    msg->net = peer->net_intro;
  }

  struct ibv_sge sge;
  sge.addr = (uintptr_t) msg;
  sge.length = sizeof(*msg);
  sge.lkey = mr_msgs->lkey;

  struct ibv_send_wr send_wr = { }; 
  send_wr.wr_id = msg - msgs;
  send_wr.opcode = IBV_WR_SEND;
  send_wr.send_flags = IBV_SEND_SIGNALED;
  send_wr.sg_list = &sge;
  send_wr.num_sge = 1;
  
  struct ibv_send_wr *bad_send_wr;
  if (ibv_post_send(cm_id->qp, &send_wr, &bad_send_wr)) {
    perror("RdmaPassIntro: ibv_post_send failed");
    return 1;
  }
  fprintf(stderr, "RdmaPassIntro: ibv_post_send done\n");

  return 0;
}