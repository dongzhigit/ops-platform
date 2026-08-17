import React, { useEffect, useState } from 'react';
import { Button, Form, Input, InputNumber, Modal, Select, Tag, Tooltip, message } from 'antd';
import { Action, AuthDiv, Breadcrumb, LinkButton, TableCard } from 'components';
import { http } from 'libs';
import {
  ACTION_LABELS,
  RISK_COLORS,
  RISK_LABELS,
  STATUS_COLORS,
  STATUS_LABELS,
} from '../constants';


function ApprovalList({pendingOnly = false}) {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState('');
  const [decision, setDecision] = useState();
  const [comment, setComment] = useState('');
  const [ttlMinutes, setTtlMinutes] = useState(30);
  const currentUserId = Number(localStorage.getItem('id'));

  function fetchRecords() {
    setLoading(true)
    const params = {};
    if (keyword.trim()) params.keyword = keyword.trim()
    if (pendingOnly) params.status = 'pending'
    else if (status) params.status = status
    http.get('/api/v1/audit/approvals/', {params})
      .then(setRecords)
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchRecords()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function cancelApproval(info) {
    Modal.confirm({
      title: '撤销审批申请',
      content: `确定撤销“${info.summary}”吗？`,
      onOk: () => http.delete('/api/v1/audit/approvals/', {params: {id: info.id}})
        .then(() => {
          message.success('审批申请已撤销')
          fetchRecords()
        })
    })
  }

  function openDecision(info, isPass) {
    setComment('')
    setTtlMinutes(30)
    setDecision({info, isPass})
  }

  function submitDecision() {
    if (!decision.isPass && !comment.trim()) return message.error('驳回时必须填写原因')
    if (decision.isPass && (!ttlMinutes || ttlMinutes < 1 || ttlMinutes > 120)) {
      return message.error('授权有效期必须在 1 到 120 分钟之间')
    }
    return http.patch(`/api/v1/audit/approvals/${decision.info.id}/decision/`, {
      is_pass: decision.isPass,
      comment: comment.trim() || undefined,
      ttl_minutes: ttlMinutes,
    }).then(() => {
      message.success(decision.isPass ? '审批已通过' : '审批已驳回')
      setDecision()
      fetchRecords()
    })
  }

  const columns = [{
    title: '申请时间',
    dataIndex: 'requested_at',
    width: 160,
  }, {
    title: '申请人',
    dataIndex: 'requester_name',
    width: 110,
  }, {
    title: '操作类型',
    dataIndex: 'action',
    width: 135,
    render: value => <Tooltip title={value}>{ACTION_LABELS[value] || value}</Tooltip>,
  }, {
    title: '目标对象',
    dataIndex: 'resource_ids',
    width: 140,
    render: value => <Tooltip title={value.map(String).join(', ')}>{value.length} 个对象</Tooltip>,
  }, {
    title: '风险',
    dataIndex: 'risk_level',
    width: 90,
    render: value => <Tag color={RISK_COLORS[value]}>{RISK_LABELS[value] || value}</Tag>,
  }, {
    title: '变更摘要',
    dataIndex: 'summary',
    ellipsis: true,
  }, {
    title: '状态',
    dataIndex: 'status',
    width: 90,
    render: value => <Tag color={STATUS_COLORS[value]}>{STATUS_LABELS[value] || value}</Tag>,
  }, {
    title: pendingOnly ? '审批' : '操作',
    width: pendingOnly ? 120 : 80,
    render: info => pendingOnly ? (
      info.requester_id === currentUserId ? <Tooltip title="申请人与审批人不能是同一用户"><span>不可自批</span></Tooltip> : (
        <Action>
          <Action.Button onClick={() => openDecision(info, true)}>通过</Action.Button>
          <Action.Button danger onClick={() => openDecision(info, false)}>驳回</Action.Button>
        </Action>
      )
    ) : (
      info.status === 'pending' ? <LinkButton danger onClick={() => cancelApproval(info)}>撤销</LinkButton> : null
    )
  }];

  return (
    <AuthDiv auth={pendingOnly ? 'admin' : undefined}>
      <Breadcrumb>
        <Breadcrumb.Item>首页</Breadcrumb.Item>
        <Breadcrumb.Item>安全中心</Breadcrumb.Item>
        <Breadcrumb.Item>{pendingOnly ? '待我审批' : '我的申请'}</Breadcrumb.Item>
      </Breadcrumb>
      <TableCard
        tKey={pendingOnly ? 'security-pending' : 'security-my-approvals'}
        rowKey="id"
        title={pendingOnly ? '待审批申请' : '我的审批申请'}
        loading={loading}
        dataSource={records}
        onReload={fetchRecords}
        actions={[
          <Input.Search
            key="keyword"
            allowClear
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onSearch={fetchRecords}
            placeholder="摘要或操作类型"
            style={{width: 220}}/>,
          pendingOnly ? null : (
            <Select key="status" allowClear value={status || undefined} onChange={setStatus}
                    placeholder="全部状态" style={{width: 120}}>
              {Object.keys(STATUS_LABELS).map(key => (
                <Select.Option key={key} value={key}>{STATUS_LABELS[key]}</Select.Option>
              ))}
            </Select>
          ),
          <Button key="search" type="primary" onClick={fetchRecords}>查询</Button>,
        ].filter(Boolean)}
        scroll={{x: 1050}}
        expandable={{
          expandedRowRender: info => (
            <div style={{padding: '0 24px'}}>
              <p><b>审批单：</b>{info.id}</p>
              <p><b>关联 ID：</b>{info.correlation_id}</p>
              <p><b>回滚方案：</b>{info.rollback_plan || '未填写'}</p>
              <p><b>审批意见：</b>{info.decision_comment || '无'}</p>
              <p><b>审批人：</b>{info.decided_by_name || '未审批'}</p>
              <p><b>有效期：</b>{info.approved_until || info.expires_at}</p>
            </div>
          )
        }}
        pagination={{showSizeChanger: true, showTotal: total => `共 ${total} 条`}}
        columns={columns}/>
      {decision ? (
        <Modal
          visible
          maskClosable={false}
          title={decision.isPass ? '通过审批' : '驳回审批'}
          okText={decision.isPass ? '确认通过' : '确认驳回'}
          okButtonProps={{danger: !decision.isPass}}
          onCancel={() => setDecision()}
          onOk={submitDecision}>
          <Form layout="vertical">
            <Form.Item label="变更摘要"><Input value={decision.info.summary} disabled/></Form.Item>
            {decision.isPass ? (
              <Form.Item required label="授权有效期（分钟）" extra="批准后必须在有效期内发起原操作，且只可使用一次。">
                <InputNumber min={1} max={120} value={ttlMinutes} onChange={setTtlMinutes} style={{width: '100%'}}/>
              </Form.Item>
            ) : null}
            <Form.Item required={!decision.isPass} label="审批意见">
              <Input.TextArea rows={4} maxLength={255} value={comment} onChange={e => setComment(e.target.value)}
                              placeholder={decision.isPass ? '可选填写' : '请输入驳回原因'}/>
            </Form.Item>
          </Form>
        </Modal>
      ) : null}
    </AuthDiv>
  )
}


export function MyApprovals() {
  return <ApprovalList/>
}


export function PendingApprovals() {
  return <ApprovalList pendingOnly/>
}
