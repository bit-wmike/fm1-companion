/* SPDX-License-Identifier: GPL-3.0-only
 * FM-1 Companion: WASI has no processes. hostsim.c (Melodee's host test harness) uses fork/pipe only in
 * its benchmark helpers, which the browser emulator never calls; these stubs just let it compile. */
#pragma once
#include <sys/types.h>
#include <unistd.h>
static inline pid_t waitpid(pid_t p, int *s, int o) { (void)p; (void)o; if (s) *s = 0; return -1; }
static inline pid_t fork(void) { return -1; }
static inline int pipe(int fd[2]) { (void)fd; return -1; }
static inline void _exit_stub(int c) { (void)c; }
#define WIFEXITED(s) 0
#define WEXITSTATUS(s) 0
#define WNOHANG 1
