export const createSingleIntervalController = ({ schedule, clear }) => {
  let timer = null

  return {
    start(callback, intervalMs) {
      if (timer !== null) {
        return false
      }
      timer = schedule(callback, intervalMs)
      return true
    },
    stop() {
      if (timer === null) {
        return false
      }
      clear(timer)
      timer = null
      return true
    },
    isRunning() {
      return timer !== null
    },
  }
}

export const shouldPollChannelAiStatus = (tabKey) => tabKey === 'manage'
