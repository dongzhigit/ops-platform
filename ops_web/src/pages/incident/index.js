import React, {useEffect, useState} from 'react';
import {
  Alert, Button, Descriptions, Drawer, Empty, Form, Input, List, Modal,
  Select, Space, Table, Tag, Timeline, Typography, message,
} from 'antd';
import {
  AlertOutlined, PlusOutlined, ReloadOutlined, SaveOutlined,
  UserSwitchOutlined,
} from '@ant-design/icons';

import {AuthButton, AuthDiv, Breadcrumb} from 'components';
import {http} from 'libs';
import styles from './index.module.less';


const statusLabel = {open: '处理中', mitigated: '已缓解', resolved: '已恢复', closed: '已关闭'};
const statusColor = {open: 'processing', mitigated: 'blue', resolved: 'green', closed: 'default'};
const severityLabel = {info: '提示', warning: '警告', critical: '严重'};
const severityColor = {info: 'blue', warning: 'orange', critical: 'red'};
const remediationStatusLabel = {
  draft: '待提交',
  approval_pending: '待审批',
  approved: '已批准',
  executing: '执行登记中',
  succeeded: '已验证成功',
  failed: '验证失败',
  rolled_back: '已回滚',
  rejected: '已驳回',
  cancelled: '已取消',
  expired: '已过期',
};
const remediationStatusColor = {
  draft: 'default',
  approval_pending: 'processing',
  approved: 'green',
  executing: 'processing',
  succeeded: 'green',
  failed: 'red',
  rolled_back: 'purple',
  rejected: 'red',
  cancelled: 'default',
  expired: 'orange',
};
const executionStatusLabel = {running: '执行登记中', succeeded: '已验证成功', failed: '验证失败', rolled_back: '已回滚'};
const executionStatusColor = {running: 'processing', succeeded: 'green', failed: 'red', rolled_back: 'purple'};

function RoomForm({visible, scope, record, onCancel, onSuccess}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!visible) return;
    form.setFieldsValue({
      id: record && record.id,
      title: record && record.title,
      severity: record && record.severity ? record.severity : 'warning',
      status: record && record.status ? record.status : 'open',
      alert_id: record && record.alert_id,
      topology_node_ids: record && record.topology_node_ids ? record.topology_node_ids : [],
      ai_investigation_ids: record && record.ai_investigation_ids ? record.ai_investigation_ids : [],
      postmortem_draft: record && record.postmortem_draft,
    });
  }, [visible, record, form]);

  function submit() {
    form.validateFields().then(values => {
      setSaving(true);
      return http.post('/api/v1/incidents/rooms/', values);
    }).then(data => {
      message.success('事件作战室已保存');
      onSuccess(data);
    }).finally(() => setSaving(false));
  }

  return (
    <Modal
      visible={visible}
      width={760}
      title={record && record.id ? '编辑事件作战室' : '新建事件作战室'}
      confirmLoading={saving}
      onOk={submit}
      onCancel={onCancel}
      destroyOnClose>
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item name="id" hidden><Input/></Form.Item>
        <Form.Item name="title" label="标题" rules={[{max: 200, message: '标题不能超过 200 个字符'}]}>
          <Input placeholder="未填写时会使用告警摘要或告警名称"/>
        </Form.Item>
        <Space size="large" align="start">
          <Form.Item name="severity" label="级别" rules={[{required: true}]}>
            <Select style={{width: 180}}>
              {Object.entries(severityLabel).map(([key, value]) => (
                <Select.Option key={key} value={key}>{value}</Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="status" label="状态" rules={[{required: true}]}>
            <Select style={{width: 180}}>
              {Object.entries(statusLabel).map(([key, value]) => (
                <Select.Option key={key} value={key}>{value}</Select.Option>
              ))}
            </Select>
          </Form.Item>
        </Space>
        <Form.Item name="alert_id" label="关联告警">
          <Select allowClear showSearch optionFilterProp="children">
            {(scope.alerts || []).map(item => (
              <Select.Option key={item.id} value={item.id}>
                {item.alert_name} / {item.host_name || '未知主机'} / {item.summary || item.status}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="topology_node_ids" label="关联拓扑节点">
          <Select mode="multiple" showSearch optionFilterProp="children" maxTagCount={4}>
            {(scope.topology_nodes || []).map(item => (
              <Select.Option key={item.id} value={item.id}>{item.name} / {item.key}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="ai_investigation_ids" label="关联 AI 调查">
          <Select mode="multiple" showSearch optionFilterProp="children" maxTagCount={3}>
            {(scope.ai_investigations || []).map(item => (
              <Select.Option key={item.id} value={item.id}>{item.question}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="postmortem_draft" label="复盘草稿">
          <Input.TextArea rows={5} maxLength={20000} showCount/>
        </Form.Item>
      </Form>
    </Modal>
  );
}


function DetailDrawer({visible, detail, onClose, onReload}) {
  const [noteForm] = Form.useForm();
  const [draftForm] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!detail) return;
    draftForm.setFieldsValue({postmortem_draft: detail.postmortem_draft || ''});
  }, [detail, draftForm]);

  if (!detail) {
    return <Drawer visible={visible} width={980} title="事件作战室" onClose={onClose}><Empty/></Drawer>;
  }

  function update(values) {
    setSaving(true);
    return http.post('/api/v1/incidents/rooms/', {id: detail.id, ...values})
      .then(onReload)
      .finally(() => setSaving(false));
  }

  function addNote() {
    noteForm.validateFields().then(values => {
      setSaving(true);
      return http.post('/api/v1/incidents/timeline/', {
        room_id: detail.id,
        message: values.message,
      });
    }).then(() => {
      noteForm.resetFields();
      message.success('备注已添加');
      onReload();
    }).finally(() => setSaving(false));
  }

  const topology = detail.topology || {nodes: [], edges: []};
  return (
    <Drawer visible={visible} width={980} title="事件作战室" onClose={onClose}>
      <Space direction="vertical" size="middle" style={{width: '100%'}}>
        <Alert showIcon type="warning" message="作战室用于集中排障证据、人工备注和复盘草稿；不会执行任何修复动作。"/>
        <Descriptions bordered size="small" column={2}>
          <Descriptions.Item label="标题" span={2}>{detail.title}</Descriptions.Item>
          <Descriptions.Item label="级别">
            <Tag color={severityColor[detail.severity]}>{severityLabel[detail.severity]}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={statusColor[detail.status]}>{statusLabel[detail.status]}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="负责人">{detail.owner ? detail.owner.name : '未认领'}</Descriptions.Item>
          <Descriptions.Item label="关联 ID">
            <Typography.Text copyable>{detail.correlation_id}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="告警" span={2}>
            {detail.alert ? `${detail.alert.alert_name} / ${detail.alert.summary || detail.alert.status}` : '未关联'}
          </Descriptions.Item>
        </Descriptions>
        <Space>
          <Button icon={<UserSwitchOutlined/>} loading={saving} onClick={() => update({claim: true})}>认领</Button>
          <Select value={detail.status} style={{width: 132}} onChange={value => update({status: value})}>
            {Object.entries(statusLabel).map(([key, value]) => (
              <Select.Option key={key} value={key}>{value}</Select.Option>
            ))}
          </Select>
        </Space>
        <div className={styles.summaryGrid}>
          <div className={styles.summaryItem}>
            <div className={styles.summaryLabel}>拓扑节点</div>
            <div className={styles.summaryValue}>{topology.nodes.length}</div>
          </div>
          <div className={styles.summaryItem}>
            <div className={styles.summaryLabel}>拓扑连线</div>
            <div className={styles.summaryValue}>{topology.edges.length}</div>
          </div>
          <div className={styles.summaryItem}>
            <div className={styles.summaryLabel}>AI 调查</div>
            <div className={styles.summaryValue}>{(detail.ai_investigations || []).length}</div>
          </div>
          <div className={styles.summaryItem}>
            <div className={styles.summaryLabel}>修复提案</div>
            <div className={styles.summaryValue}>{(detail.remediation_proposals || []).length}</div>
          </div>
        </div>
        <div className={styles.section}>
          <div className={styles.sectionTitle}>拓扑范围</div>
          <List
            size="small"
            dataSource={topology.nodes}
            locale={{emptyText: '暂无拓扑节点'}}
            renderItem={item => (
              <List.Item>
                <Space>
                  <Tag>{item.type}</Tag>
                  <span>{item.name}</span>
                  <Tag color={item.status === 'healthy' ? 'green' : item.status === 'critical' ? 'red' : 'default'}>
                    {item.status}
                  </Tag>
                </Space>
              </List.Item>
            )}/>
        </div>
        <div className={styles.section}>
          <div className={styles.sectionTitle}>AI 调查</div>
          <List
            size="small"
            dataSource={detail.ai_investigations || []}
            locale={{emptyText: '暂无 AI 调查'}}
            renderItem={item => (
              <List.Item>
                <List.Item.Meta
                  title={item.question}
                  description={`${item.status} / ${item.model} / ${item.completed_at || item.created_at}`}/>
              </List.Item>
            )}/>
        </div>
        <div className={styles.section}>
          <div className={styles.sectionTitle}>修复闭环</div>
          <List
            size="small"
            dataSource={detail.remediation_proposals || []}
            locale={{emptyText: '暂无修复提案'}}
            renderItem={item => (
              <List.Item>
                <Space direction="vertical" size={4} style={{width: '100%'}}>
                  <Space>
                    <Typography.Text strong>{item.title}</Typography.Text>
                    <Tag color={remediationStatusColor[item.status]}>{remediationStatusLabel[item.status]}</Tag>
                    <Tag>{item.risk_level}</Tag>
                    {item.approval_id && <Typography.Text code copyable>{item.approval_id}</Typography.Text>}
                  </Space>
                  <Typography.Text>{item.summary}</Typography.Text>
                  {(item.executions || []).map(execution => (
                    <div key={execution.id} className={styles.timelineItem}>
                      <Space>
                        <Tag color={executionStatusColor[execution.status]}>{executionStatusLabel[execution.status]}</Tag>
                        <span>{execution.execution_summary}</span>
                        {execution.execution_ref && <Typography.Text code copyable>{execution.execution_ref}</Typography.Text>}
                      </Space>
                      {execution.validation_result && (
                        <div className={styles.timelineMeta}>验证：{execution.validation_result}</div>
                      )}
                      {execution.rollback_result && (
                        <div className={styles.timelineMeta}>回滚：{execution.rollback_result}</div>
                      )}
                    </div>
                  ))}
                </Space>
              </List.Item>
            )}/>
        </div>
        <div className={styles.section}>
          <div className={styles.sectionTitle}>时间线</div>
          <Timeline>
            {(detail.timeline || []).map(item => (
              <Timeline.Item key={item.id}>
                <div className={styles.timelineItem}>
                  <div>{item.message}</div>
                  <div className={styles.timelineMeta}>{item.type} / {item.created_by.name} / {item.created_at}</div>
                </div>
              </Timeline.Item>
            ))}
          </Timeline>
          <Form form={noteForm} layout="vertical">
            <Form.Item name="message" rules={[{required: true, message: '请输入备注'}]}>
              <Input.TextArea rows={3} maxLength={4000} showCount placeholder="记录排查结论、人工判断或交接信息"/>
            </Form.Item>
            <Button loading={saving} onClick={addNote}>添加备注</Button>
          </Form>
        </div>
        <div className={styles.section}>
          <div className={styles.sectionTitle}>复盘草稿</div>
          <Form form={draftForm} layout="vertical">
            <Form.Item name="postmortem_draft">
              <Input.TextArea className={styles.postmortem} maxLength={20000} showCount/>
            </Form.Item>
            <Button icon={<SaveOutlined/>} loading={saving}
                    onClick={() => draftForm.validateFields().then(update)}>保存复盘草稿</Button>
          </Form>
        </div>
      </Space>
    </Drawer>
  );
}


export default function IncidentIndex() {
  const [scope, setScope] = useState({alerts: [], topology_nodes: [], ai_investigations: []});
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const [detail, setDetail] = useState(null);

  function reload() {
    setLoading(true);
    return Promise.all([
      http.get('/api/v1/incidents/scope/').then(setScope),
      http.get('/api/v1/incidents/rooms/').then(setRooms),
    ]).finally(() => setLoading(false));
  }

  function openDetail(record) {
    return http.get('/api/v1/incidents/rooms/', {params: {id: record.id}}).then(setDetail);
  }

  useEffect(() => {
    reload();
  }, []);

  const columns = [{
    title: '标题',
    dataIndex: 'title',
    ellipsis: true,
  }, {
    title: '级别',
    dataIndex: 'severity',
    width: 90,
    render: value => <Tag color={severityColor[value]}>{severityLabel[value]}</Tag>,
  }, {
    title: '状态',
    dataIndex: 'status',
    width: 100,
    render: value => <Tag color={statusColor[value]}>{statusLabel[value]}</Tag>,
  }, {
    title: '负责人',
    width: 120,
    render: record => record.owner ? record.owner.name : '未认领',
  }, {
    title: '告警',
    width: 220,
    render: record => record.alert ? record.alert.alert_name : '未关联',
  }, {
    title: '更新时间',
    dataIndex: 'updated_at',
    width: 170,
  }, {
    title: '操作',
    width: 90,
    render: record => <Button type="link" onClick={() => openDetail(record)}>查看</Button>,
  }];

  return (
    <AuthDiv auth="incident.room.view|incident.room.manage">
      <Breadcrumb>
        <Breadcrumb.Item>首页</Breadcrumb.Item>
        <Breadcrumb.Item>事件作战室</Breadcrumb.Item>
      </Breadcrumb>
      <div className={styles.toolbar}>
        <Space>
          <Button icon={<ReloadOutlined/>} loading={loading} onClick={reload}>刷新</Button>
        </Space>
        <AuthButton auth="incident.room.manage" type="primary" icon={<PlusOutlined/>} onClick={() => setFormVisible(true)}>
          新建事件
        </AuthButton>
      </div>
      <div className={styles.panel}>
        <AlertOutlined style={{marginRight: 8}}/>
        <Typography.Text strong>事件列表</Typography.Text>
        <Table
          style={{marginTop: 12}}
          rowKey="id"
          dataSource={rooms}
          loading={loading}
          columns={columns}
          pagination={{showSizeChanger: true, showTotal: total => `共 ${total} 条`}}/>
      </div>
      <RoomForm
        visible={formVisible}
        scope={scope}
        record={{}}
        onCancel={() => setFormVisible(false)}
        onSuccess={data => {
          setFormVisible(false);
          setDetail(data);
          reload();
        }}/>
      <DetailDrawer
        visible={Boolean(detail)}
        detail={detail}
        onClose={() => setDetail(null)}
        onReload={() => detail && openDetail(detail).then(reload)}/>
    </AuthDiv>
  );
}
