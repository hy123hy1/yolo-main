export type SingleIntervalScheduler<TTimer> = {
  schedule(callback: TimerHandler, intervalMs: number): TTimer
  clear(timer: TTimer): void
}

export type SingleIntervalController<TTimer> = {
  start(callback: TimerHandler, intervalMs: number): boolean
  stop(): boolean
  isRunning(): boolean
}

export function createSingleIntervalController<TTimer>(
  scheduler: SingleIntervalScheduler<TTimer>
): SingleIntervalController<TTimer>

export function shouldPollChannelAiStatus(tabKey: string): boolean
