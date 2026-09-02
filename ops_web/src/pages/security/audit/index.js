import React, { useEffect, useState } from 'react';
import { Button, Input, Modal, Select, Tag, Typography } from 'antd';
import { AuthDiv, Breadcrumb, TableCard } from 'components';
import { http } from 'libs';
import { ACTION_LABELS, RESULT_COLORS, RESULT_LABELS } from '../constants';


export default function AuditEvents() {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [correlationId, setCorrelationId] = useState('');
  const [action, setAction] = useState();
  const [result, setResult] = useState();

  function fetchRecords() {
    setLoading(true)
    const params = {};
    if (correlationId.trim()) params.correlation_id = correlationId.trim()
    if (action) params.action = action
    if (result) params.result = result
    http.get('/api/v1/audit/events/', {params})
      .then(setRecords)
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchRecords()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function verifyChain() {
    http.get('/api/v1/audit/verify/').then(data => {
      if (data.valid) Modal.success({title: '审计链校验通过', content: data.message})
      else Modal.error({title: '审计链校验失败', content: data.message})
    })
  }

  const columns = [{
    title: '序号',
    dataIndex: 'sequence',
    width: 80,
  }, {
    title: '时间',
    dataIndex: 'created_at',
    width: 160,
  }, {
    title: '操作人',
    dataIndex: 'actor_name',
    width: 110,
  }, {
    title: '操作类型',
    dataIndex: 'action',
    width: 145,
    render: value => ACTION_LABELS[value] || value,
  }, {
    title: '资源',
    width: 160,
    render: info => `${info.resource_type}${info.resource_id ? ` / ${info.resource_id}` : ''}`,
  }, {
    title: '结果',
    dataIndex: 'result',
    width: 90,
    render: value => <Tag color={RESULT_COLORS[value]}>{RESULT_LABELS[value] || value}</Tag>,
  }, {
    title: '来源 IP',
    dataIndex: 'source_ip',
    width: 130,
  }, {
    title: '关联 ID',
    dataIndex: 'correlation_id',
    ellipsis: true,
    render: value => <Typography.Text copyable={{text: value}}>{value}</Typography.Text>,
  }];

  return (
    <AuthDiv auth="admin">
      <Breadcrumb>
        <Breadcrumb.Item>首页</Breadcrumb.Item>
        <Breadcrumb.Item>安全中心</Breadcrumb.Item>
        <Breadcrumb.Item>审计日志</Breadcrumb.Item>
      </Breadcrumb>
      <TableCard
        tKey="security-audit-events"
        rowKey="id"
        title="只追加审计日志"
        loading={loading}
        dataSource={records}
        onReload={fetchRecords}
        actions={[
          <Input.Search key="correlation" allowClear value={correlationId}
                        onChange={e => setCorrelationId(e.target.value)} onSearch={fetchRecords}
                        placeholder="关联 ID" style={{width: 220}}/>,
          <Select key="action" allowClear value={action} onChange={setAction}
                  placeholder="全部操作" style={{width: 150}}>
            {Object.keys(ACTION_LABELS).map(key => (
              <Select.Option key={key} value={key}>{ACTION_LABELS[key]}</Select.Option>
            ))}
          </Select>,
          <Select key="result" allowClear value={result} onChange={setResult}
                  placeholder="全部结果" style={{width: 120}}>
            {Object.keys(RESULT_LABELS).map(key => (
              <Select.Option key={key} value={key}>{RESULT_LABELS[key]}</Select.Option>
            ))}
          </Select>,
          <Button key="search" type="primary" onClick={fetchRecords}>查询</Button>,
          <Button key="verify" onClick={verifyChain}>校验审计链</Button>,
        ]}
        scroll={{x: 1100}}
        expandable={{
          expandedRowRender: info => (
            <div style={{padding: '0 24px'}}>
              <p><b>请求：</b>{[info.request_method, info.request_path].filter(Boolean).join(' ') || '后台任务'}</p>
              <p><b>事件哈希：</b><Typography.Text copyable>{info.event_hash}</Typography.Text></p>
              <p><b>前置哈希：</b><Typography.Text copyable>{info.previous_hash || '创世事件'}</Typography.Text></p>
              <pre style={{whiteSpace: 'pre-wrap', wordBreak: 'break-all'}}>{JSON.stringify(info.details, null, 2)}</pre>
            </div>
          )
        }}
        pagination={{showSizeChanger: true, showTotal: total => `共 ${total} 条`}}
        columns={columns}/>
    </AuthDiv>
  )
}
