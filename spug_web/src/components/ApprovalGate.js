import React, { useEffect, useState } from 'react';
import { Alert, Descriptions, Form, Input, Modal, Select, Spin, Tag, message } from 'antd';
import { http } from 'libs';
import { ACTION_LABELS, RISK_COLORS, RISK_LABELS } from 'pages/security/constants';


export default function ApprovalGate(props) {
  const {operation, defaultSummary, onCancel, onComplete, onExecute} = props;
  const [preview, setPreview] = useState();
  const [summary, setSummary] = useState((defaultSummary || '').slice(0, 255));
  const [rollbackPlan, setRollbackPlan] = useState('');
  const [approvalId, setApprovalId] = useState();
  const [submitting, setSubmitting] = useState(false);

  function execute(id) {
    setSubmitting(true)
    return Promise.resolve()
      .then(() => onExecute(id))
      .then(onComplete || onCancel)
      .catch(() => setSubmitting(false))
  }

  useEffect(() => {
    let active = true;
    setPreview()
    setSummary((defaultSummary || '').slice(0, 255))
    setRollbackPlan('')
    setApprovalId()
    http.post('/api/v1/audit/preview/', operation)
      .then(data => {
        if (!active) return
        setPreview(data)
        if (!data.approval_required) {
          execute()
        } else if (data.matching_approvals.length) {
          setApprovalId(data.matching_approvals[0].id)
        }
      })
      .catch(() => active && onCancel())
    return () => {
      active = false
    }
    // operation is replaced for every new operation request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operation])

  function createApproval() {
    if (!summary.trim()) return message.error('请输入变更摘要')
    if (preview.rollback_required && !rollbackPlan.trim()) {
      return message.error('严重风险操作必须填写回滚方案')
    }
    setSubmitting(true)
    http.post('/api/v1/audit/approvals/', {
      ...operation,
      summary: summary.trim(),
      rollback_plan: rollbackPlan.trim() || undefined,
    }).then(() => {
      message.success('审批申请已提交，请等待另一名管理员审批')
      onCancel()
    }).catch(() => setSubmitting(false))
  }

  const matches = preview ? preview.matching_approvals : [];
  const hasApproval = matches.length > 0;
  const risk = preview ? preview.risk_level : undefined;
  return (
    <Modal
      visible
      width={680}
      maskClosable={false}
      closable={!submitting}
      title="操作风险检查"
      okText={hasApproval ? '使用审批并执行' : '提交审批申请'}
      okButtonProps={{
        disabled: !preview || !preview.approval_required || (hasApproval ? !approvalId : !summary.trim())
      }}
      confirmLoading={submitting}
      onCancel={submitting ? undefined : onCancel}
      onOk={() => hasApproval ? execute(approvalId) : createApproval()}>
      {!preview ? (
        <div style={{padding: 48, textAlign: 'center'}}><Spin tip="正在进行服务端风险检查..."/></div>
      ) : !preview.approval_required ? (
        <div style={{padding: 48, textAlign: 'center'}}><Spin tip="风险检查通过，正在执行..."/></div>
      ) : (
        <React.Fragment>
          <Alert
            showIcon
            type={risk === 'critical' ? 'error' : 'warning'}
            message={`服务端判定为${RISK_LABELS[risk] || risk}操作，需要审批后执行`}
            description="审批单与操作类型、目标对象和完整参数绑定，有效期内仅可使用一次。"
            style={{marginBottom: 20}}/>
          <Descriptions bordered size="small" column={1} style={{marginBottom: 20}}>
            <Descriptions.Item label="操作类型">{ACTION_LABELS[operation.action] || operation.action}</Descriptions.Item>
            <Descriptions.Item label="风险等级">
              <Tag color={RISK_COLORS[risk]}>{RISK_LABELS[risk] || risk}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="目标对象">{operation.resource_ids.map(String).join(', ')}</Descriptions.Item>
          </Descriptions>
          {hasApproval ? (
            <Form layout="vertical">
              <Form.Item required label="可用审批单">
                <Select value={approvalId} onChange={setApprovalId}>
                  {matches.map(item => (
                    <Select.Option key={item.id} value={item.id}>
                      {item.summary}（有效至 {item.approved_until}）
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>
              <Alert type="success" showIcon message="已找到与本次操作完全匹配的有效审批单"/>
            </Form>
          ) : (
            <Form layout="vertical">
              <Form.Item required label="变更摘要">
                <Input
                  maxLength={255}
                  value={summary}
                  onChange={e => setSummary(e.target.value)}
                  placeholder="说明操作目的、影响范围和预期结果"/>
              </Form.Item>
              <Form.Item required={preview.rollback_required} label="回滚方案">
                <Input.TextArea
                  rows={4}
                  value={rollbackPlan}
                  onChange={e => setRollbackPlan(e.target.value)}
                  placeholder={preview.rollback_required ? '严重风险操作必须填写可执行的回滚方案' : '建议填写异常时的恢复步骤'}/>
              </Form.Item>
            </Form>
          )}
        </React.Fragment>
      )}
    </Modal>
  )
}
