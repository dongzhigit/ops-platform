export const ACTION_LABELS = {
  'exec.run': '批量执行命令',
  'file.read': '读取文件',
  'file.write': '写入文件',
  'file.cleanup': '清理上传临时文件',
  'file.delete': '删除文件',
  'file.distribute': '文件分发',
  'schedule.write': '配置计划任务',
  'schedule.run': '执行计划任务',
  'ssh.connect': '连接终端',
  'deploy.run': '发布部署',
  'config.write': '修改配置',
  'host.view': '查看主机',
}

export const RISK_LABELS = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  critical: '严重风险',
}

export const RISK_COLORS = {
  low: 'green',
  medium: 'blue',
  high: 'orange',
  critical: 'red',
}

export const STATUS_LABELS = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
  cancelled: '已撤销',
  consumed: '已使用',
  expired: '已过期',
}

export const STATUS_COLORS = {
  pending: 'gold',
  approved: 'green',
  rejected: 'red',
  cancelled: 'default',
  consumed: 'blue',
  expired: 'default',
}

export const RESULT_LABELS = {
  requested: '已申请',
  approved: '已批准',
  rejected: '已驳回',
  cancelled: '已撤销',
  denied: '已拒绝',
  queued: '已入队',
  started: '已开始',
  succeeded: '成功',
  failed: '失败',
}

export const RESULT_COLORS = {
  requested: 'gold',
  approved: 'green',
  rejected: 'red',
  cancelled: 'default',
  denied: 'red',
  queued: 'blue',
  started: 'processing',
  succeeded: 'green',
  failed: 'red',
}
