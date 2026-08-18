import React, { useEffect, useState } from 'react';
import {
  Button, Descriptions, Drawer, Input, InputNumber, Modal, Radio,
  Select, Space, Table, Tag, Timeline, message,
} from 'antd';
import moment from 'moment';
import { hasPermission, http } from 'libs';


const severityMap = {
  info: ['blue', '提示'],
  warning: ['orange', '警告'],
  critical: ['red', '严重'],
};


export default function AlertEvents() {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [severity, setSeverity] = useState('');
  const [detail, setDetail] = useState(null);
  const [silenceRecord, setSilenceRecord] = useState(null);
  const [minutes, setMinutes] = useState(60);
  const [reason, setReason] = useState('');
  const currentUserId = Number(localStorage.getItem('id'));

  function fetchRecords() {
    setLoading(true);
    return http.get('/api/v1/observability/alerts/', {params: {status, severity}})
      .then(setRecords)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    fetchRecords();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, severity]);

  function act(record, action, extra = {}) {
    return http.patch('/api/v1/observability/alerts/', {id: record.id, action, ...extra})
      .then(() => {
        message.success('告警事件已更新');
        fetchRecords();
      });
  }

  function showDetail(record) {
    http.get('/api/v1/observability/alerts/', {params: {id: record.id}})
      .then(setDetail);
  }

  function submitSilence() {
    if (!reason.trim()) {
      message.error('请输入静默原因');
      return;
    }
    act(silenceRecord, 'silence', {minutes, reason: reason.trim()})
      .then(() => {
        setSilenceRecord(null);
        setReason('');
      });
  }

  const columns = [{
    title: '级别',
    dataIndex: 'severity',
    width: 80,
    render: value => <Tag color={severityMap[value][0]}>{severityMap[value][1]}</Tag>,
  }, {
    title: '状态',
    dataIndex: 'status',
    width: 95,
    render: (value, record) => (
      <Space size={4}>
        <Tag color={value === 'firing' ? 'red' : 'green'}>{value === 'firing' ? '告警中' : '已恢复'}</Tag>
        {record.is_silenced && <Tag color="purple">静默</Tag>}
      </Space>
    ),
  }, {
    title: '告警名称',
    dataIndex: 'alert_name',
    render: (value, record) => (
      <div>
        <div>{value}</div>
        <div style={{fontSize: 12, color: '#888'}}>{record.summary || record.description}</div>
      </div>
    ),
  }, {
    title: '主机',
    dataIndex: 'host',
    render: value => value ? `${value.name}（${value.hostname}）` : '未关联资产',
  }, {
    title: '发生/恢复',
    width: 180,
    render: record => (
      <div>
        <div>{moment(record.starts_at).format('YYYY-MM-DD HH:mm:ss')}</div>
        <div style={{fontSize: 12, color: '#888'}}>
          {record.ends_at ? moment(record.ends_at).format('YYYY-MM-DD HH:mm:ss') : '尚未恢复'}
        </div>
      </div>
    ),
  }, {
    title: '聚合次数',
    dataIndex: 'occurrence_count',
    width: 90,
  }, {
    title: '认领人',
    dataIndex: 'claimed_by',
    width: 100,
    render: value => value?.name || '未认领',
  }, {
    title: '操作',
    width: 250,
    render: record => (
      <Space size={4}>
        <Button type="link" onClick={() => showDetail(record)}>详情</Button>
        {hasPermission('alarm.event.claim') && (
          record.claimed_by?.id === currentUserId ? (
            <Button type="link" onClick={() => act(record, 'unclaim')}>取消认领</Button>
          ) : (
            <Button type="link" disabled={Boolean(record.claimed_by)} onClick={() => act(record, 'claim')}>认领</Button>
          )
        )}
        {hasPermission('alarm.event.silence') && (
          record.is_silenced ? (
            <Button type="link" onClick={() => act(record, 'unsilence')}>取消静默</Button>
          ) : (
            <Button type="link" onClick={() => setSilenceRecord(record)}>静默</Button>
          )
        )}
      </Space>
    ),
  }];

  return (
    <>
      <div style={{display: 'flex', justifyContent: 'space-between', marginBottom: 16}}>
        <Space>
          <Radio.Group value={status} onChange={event => setStatus(event.target.value)}>
            <Radio.Button value="">全部状态</Radio.Button>
            <Radio.Button value="firing">告警中</Radio.Button>
            <Radio.Button value="resolved">已恢复</Radio.Button>
          </Radio.Group>
          <Select allowClear style={{width: 130}} value={severity || undefined} onChange={value => setSeverity(value || '')} placeholder="全部级别">
            <Select.Option value="critical">严重</Select.Option>
            <Select.Option value="warning">警告</Select.Option>
            <Select.Option value="info">提示</Select.Option>
          </Select>
        </Space>
        <Button onClick={fetchRecords}>刷新</Button>
      </div>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={records}
        loading={loading}
        pagination={{showSizeChanger: true, showTotal: total => `共 ${total} 条`}}/>

      <Modal
        visible={Boolean(silenceRecord)}
        title="创建 Alertmanager 静默"
        onOk={submitSilence}
        onCancel={() => setSilenceRecord(null)}>
        <Space direction="vertical" style={{width: '100%'}}>
          <div>静默时长（分钟）</div>
          <InputNumber min={5} max={10080} value={minutes} onChange={setMinutes} style={{width: '100%'}}/>
          <div>原因</div>
          <Input.TextArea maxLength={255} rows={3} value={reason} onChange={event => setReason(event.target.value)}/>
        </Space>
      </Modal>

      <Drawer
        width={680}
        visible={Boolean(detail)}
        title="告警事件详情"
        onClose={() => setDetail(null)}>
        {detail && (
          <>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="告警">{detail.alert_name}</Descriptions.Item>
              <Descriptions.Item label="状态">{detail.status}</Descriptions.Item>
              <Descriptions.Item label="主机">{detail.host?.name || '未关联资产'}</Descriptions.Item>
              <Descriptions.Item label="摘要">{detail.summary || detail.description || '—'}</Descriptions.Item>
              <Descriptions.Item label="Fingerprint">{detail.fingerprint}</Descriptions.Item>
              <Descriptions.Item label="事件关联 ID">{detail.correlation_id}</Descriptions.Item>
              <Descriptions.Item label="标签"><pre style={{whiteSpace: 'pre-wrap'}}>{JSON.stringify(detail.labels, null, 2)}</pre></Descriptions.Item>
            </Descriptions>
            <h3 style={{marginTop: 24}}>事件时间线</h3>
            <Timeline>
              {(detail.transitions || []).map(item => (
                <Timeline.Item key={item.id}>
                  {moment(item.created_at).format('YYYY-MM-DD HH:mm:ss')} · {item.type}
                  {item.actor ? ` · ${item.actor.name}` : ''}
                </Timeline.Item>
              ))}
            </Timeline>
          </>
        )}
      </Drawer>
    </>
  );
}
