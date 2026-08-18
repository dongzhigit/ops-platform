import React, { useEffect, useState } from 'react';
import { Checkbox, Form, Input, InputNumber, Modal, Select, Switch, message } from 'antd';
import { http } from 'libs';


const notifyModes = [
  ['1', '微信'], ['2', '短信'], ['3', '钉钉'], ['4', '邮件'],
  ['5', '企业微信'], ['6', '电话'], ['7', '飞书'],
];


export default function MetricTargetForm({visible, record, onCancel, onSuccess}) {
  const [form] = Form.useForm();
  const [hosts, setHosts] = useState([]);
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!visible) return;
    http.all([
      http.get('/api/host/'),
      http.get('/api/alarm/group/'),
    ]).then(http.spread((hostData, groupData) => {
      setHosts(hostData);
      setGroups(groupData);
    }));
    form.setFieldsValue({
      id: record?.id,
      host_id: record?.host?.id,
      exporter_address: record?.exporter_address,
      exporter_port: record?.exporter_port || 9100,
      scheme: record?.scheme || 'http',
      metrics_path: record?.metrics_path || '/metrics',
      notify_grp: record?.notify_grp || [],
      notify_mode: record?.notify_mode || [],
      is_active: record?.is_active === undefined ? true : record.is_active,
    });
  }, [visible, record, form]);

  function submit() {
    form.validateFields().then(values => {
      setLoading(true);
      http.post('/api/v1/observability/targets/', values)
        .then(() => {
          message.success('指标采集目标已保存');
          onSuccess();
        })
        .finally(() => setLoading(false));
    });
  }

  return (
    <Modal
      destroyOnClose
      visible={visible}
      title={record?.id ? '编辑指标采集目标' : '新增指标采集目标'}
      confirmLoading={loading}
      onOk={submit}
      onCancel={onCancel}>
      <Form form={form} layout="vertical">
        <Form.Item name="id" hidden><Input/></Form.Item>
        <Form.Item name="host_id" label="关联主机" rules={[{required: true, message: '请选择主机'}]}>
          <Select
            showSearch
            optionFilterProp="children"
            disabled={Boolean(record?.id)}
            placeholder="选择已授权主机">
            {hosts.map(item => (
              <Select.Option key={item.id} value={item.id}>
                {item.name}（{item.hostname}）
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item
          name="exporter_address"
          label="Exporter 地址"
          extra="留空时使用资产中的主机名/IP；容器或代理场景可填写 Prometheus 实际可达地址。">
          <Input placeholder="例如 10.0.0.12 或 node-exporter"/>
        </Form.Item>
        <Form.Item label="采集端点" style={{marginBottom: 0}}>
          <Input.Group compact>
            <Form.Item name="scheme" noStyle>
              <Select style={{width: '25%'}}>
                <Select.Option value="http">HTTP</Select.Option>
                <Select.Option value="https">HTTPS</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item name="exporter_port" noStyle>
              <InputNumber min={1} max={65535} style={{width: '30%'}}/>
            </Form.Item>
            <Form.Item name="metrics_path" noStyle>
              <Input style={{width: '45%'}} placeholder="/metrics"/>
            </Form.Item>
          </Input.Group>
        </Form.Item>
        <Form.Item name="notify_grp" label="告警联系组">
          <Select mode="multiple" allowClear placeholder="可选；未选择时只生成站内事件">
            {groups.map(item => (
              <Select.Option key={item.id} value={item.id}>{item.name}</Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="notify_mode" label="告警方式">
          <Checkbox.Group options={notifyModes.map(([value, label]) => ({value, label}))}/>
        </Form.Item>
        <Form.Item name="is_active" label="启用采集" valuePropName="checked">
          <Switch/>
        </Form.Item>
      </Form>
    </Modal>
  );
}
