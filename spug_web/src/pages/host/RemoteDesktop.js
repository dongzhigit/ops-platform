import React, { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Divider,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Radio,
  Select,
  Space,
  Switch,
  Tag,
  message,
} from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import { hasPermission, http } from 'libs';


const DEFAULTS = {
  rdp: {port: 3389, security: 'nla', resize_method: 'display-update', ignore_cert: false},
  vnc: {port: 5900, color_depth: 24, cursor: 'remote', read_only: false},
};


export default function RemoteDesktop({host, onClose}) {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [endpoints, setEndpoints] = useState([]);
  const [bindings, setBindings] = useState([]);
  const [selectedId, setSelectedId] = useState();
  const [editing, setEditing] = useState();
  const [protocol, setProtocol] = useState('rdp');
  const isAdmin = hasPermission('admin');

  useEffect(() => {
    if (!host) return;
    fetchEndpoints();
    if (isAdmin) {
      http.get('/api/v1/assets/bindings/', {params: {host_id: host.id}})
        .then(items => setBindings(items.filter(item => ['rdp', 'vnc'].includes(item.identity.protocol))));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host]);

  function fetchEndpoints() {
    setLoading(true);
    return http.get('/api/v1/gateway/endpoints/', {params: {host_id: host.id}})
      .then(items => {
        setEndpoints(items);
        const selected = items.find(item => item.id === selectedId && item.is_active)
          || items.find(item => item.is_active);
        setSelectedId(selected ? selected.id : undefined);
      })
      .finally(() => setLoading(false));
  }

  function showEditor(endpoint) {
    const item = endpoint || {};
    const nextProtocol = item.protocol || 'rdp';
    const defaults = DEFAULTS[nextProtocol];
    const itemSettings = item.settings || {};
    setProtocol(nextProtocol);
    setEditing(item.id || 'new');
    form.setFieldsValue({
      id: item.id,
      display_name: item.display_name || `${host.name} ${nextProtocol.toUpperCase()}`,
      protocol: nextProtocol,
      port: item.port || defaults.port,
      identity_id: item.identity && item.identity.id,
      is_active: item.id ? item.is_active : true,
      security: itemSettings.security || defaults.security,
      resize_method: itemSettings['resize-method'] || defaults.resize_method,
      ignore_cert: itemSettings['ignore-cert'] || false,
      color_depth: itemSettings['color-depth'] || defaults.color_depth,
      cursor: itemSettings.cursor || defaults.cursor,
      read_only: itemSettings['read-only'] || false,
    });
  }

  function changeProtocol(value) {
    setProtocol(value);
    form.setFieldsValue({...DEFAULTS[value], identity_id: undefined});
  }

  function saveEndpoint() {
    form.validateFields().then(values => {
      const settings = values.protocol === 'rdp' ? {
        security: values.security,
        'resize-method': values.resize_method,
        'ignore-cert': values.ignore_cert,
      } : {
        'color-depth': values.color_depth,
        cursor: values.cursor,
        'read-only': values.read_only,
      };
      setLoading(true);
      return http.post('/api/v1/gateway/endpoints/', {
        id: values.id,
        host_id: host.id,
        protocol: values.protocol,
        port: values.port,
        identity_id: values.identity_id,
        display_name: values.display_name,
        is_active: values.is_active,
        settings,
      }).then(() => {
        message.success('远程桌面端点已保存');
        setEditing(undefined);
        return fetchEndpoints();
      }).finally(() => setLoading(false));
    });
  }

  function deleteEndpoint(endpoint) {
    Modal.confirm({
      title: '删除远程桌面端点',
      content: `确定删除【${endpoint.display_name}】？已有会话记录时将改为停用。`,
      onOk: () => http.delete('/api/v1/gateway/endpoints/', {params: {id: endpoint.id}})
        .then(result => {
          message.success(result.operation === 'deleted' ? '端点已删除' : '端点已有会话记录，已安全停用');
          return fetchEndpoints();
        }),
    });
  }

  function connect() {
    if (!selectedId) return message.warning('请选择可用的远程桌面端点');
    const popup = window.open('about:blank', `spug-remote-${Date.now()}`);
    if (popup) {
      popup.opener = null;
      popup.document.title = '正在启动远程桌面';
      popup.document.body.innerText = '正在校验权限并启动安全连接…';
    }
    setConnecting(true);
    http.post('/api/v1/gateway/sessions/', {endpoint_id: selectedId})
      .then(session => {
        if (popup) {
          popup.location.replace(session.launch_url);
        } else {
          Modal.info({
            title: '浏览器阻止了新窗口',
            content: <a href={session.launch_url} target="_blank" rel="noopener noreferrer">点击这里启动远程桌面</a>,
          });
        }
      })
      .catch(() => popup && popup.close())
      .finally(() => setConnecting(false));
  }

  const identityOptions = bindings
    .filter(item => item.identity.protocol === protocol)
    .map(item => ({value: item.identity.id, label: `${item.identity.name} (${item.identity.username})`}));

  return <Modal
    visible={Boolean(host)}
    width={700}
    title={host ? `远程桌面 · ${host.name}` : '远程桌面'}
    onCancel={onClose}
    footer={[
      <Button key="cancel" onClick={onClose}>关闭</Button>,
      <Button key="connect" type="primary" loading={connecting} disabled={!selectedId} onClick={connect}>
        安全连接
      </Button>,
    ]}>
    <Alert
      showIcon
      type="info"
      message="密码不会发送给 Spug 前端；启动票据默认 60 秒内有效且只能消费一次。"
      style={{marginBottom: 16}}/>
    <Radio.Group value={selectedId} onChange={event => setSelectedId(event.target.value)} style={{width: '100%'}}>
      <List
        loading={loading}
        locale={{emptyText: '当前主机没有可用的 RDP/VNC 端点'}}
        dataSource={endpoints}
        renderItem={item => <List.Item actions={isAdmin ? [
          <Button key="edit" type="link" icon={<EditOutlined/>} onClick={() => showEditor(item)}>编辑</Button>,
          <Button key="delete" type="link" danger icon={<DeleteOutlined/>} onClick={() => deleteEndpoint(item)}>删除</Button>,
        ] : []}>
          <Radio value={item.id} disabled={!item.is_active}>
            <Space>
              <strong>{item.display_name}</strong>
              <Tag color={item.protocol === 'rdp' ? 'blue' : 'purple'}>{item.protocol.toUpperCase()}</Tag>
              <span>{item.host.hostname}:{item.port}</span>
              {!item.is_active && <Tag>已停用</Tag>}
            </Space>
          </Radio>
        </List.Item>}/>
    </Radio.Group>

    {isAdmin && <React.Fragment>
      <Divider orientation="left">
        <Button type="link" icon={<PlusOutlined/>} onClick={() => showEditor()}>配置端点</Button>
      </Divider>
      {editing && <Form form={form} layout="vertical">
        <Form.Item name="id" hidden><Input/></Form.Item>
        <Space align="start" size="middle">
          <Form.Item label="协议" name="protocol" rules={[{required: true}]}>
            <Select style={{width: 100}} onChange={changeProtocol}>
              <Select.Option value="rdp">RDP</Select.Option>
              <Select.Option value="vnc">VNC</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item label="端口" name="port" rules={[{required: true}]}>
            <InputNumber min={1} max={65535}/>
          </Form.Item>
          <Form.Item label="启用" name="is_active" valuePropName="checked">
            <Switch/>
          </Form.Item>
        </Space>
        <Form.Item label="显示名称" name="display_name" rules={[{required: true, message: '请输入显示名称'}]}>
          <Input maxLength={100}/>
        </Form.Item>
        <Form.Item
          label="已绑定身份"
          name="identity_id"
          rules={[{required: true, message: `请先为主机绑定 ${protocol.toUpperCase()} 密码身份`}]}>
          <Select options={identityOptions} placeholder={`选择 ${protocol.toUpperCase()} 身份`}/>
        </Form.Item>
        {protocol === 'rdp' ? <Space align="start" size="middle">
          <Form.Item label="安全模式" name="security">
            <Select style={{width: 120}}>
              <Select.Option value="nla">NLA</Select.Option>
              <Select.Option value="nla-ext">NLA-EXT</Select.Option>
              <Select.Option value="tls">TLS</Select.Option>
              <Select.Option value="rdp">RDP</Select.Option>
              <Select.Option value="any">自动协商</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item label="窗口调整" name="resize_method">
            <Select style={{width: 150}}>
              <Select.Option value="display-update">动态调整</Select.Option>
              <Select.Option value="reconnect">重新连接</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item label="忽略证书错误" name="ignore_cert" valuePropName="checked"><Switch/></Form.Item>
        </Space> : <Space align="start" size="middle">
          <Form.Item label="色深" name="color_depth"><Select style={{width: 100}} options={[8, 16, 24, 32].map(value => ({value, label: `${value} 位`}))}/></Form.Item>
          <Form.Item label="光标" name="cursor"><Select style={{width: 100}} options={[{value: 'remote', label: '远端'}, {value: 'local', label: '本地'}]}/></Form.Item>
          <Form.Item label="只读" name="read_only" valuePropName="checked"><Switch/></Form.Item>
        </Space>}
        {identityOptions.length === 0 && <Alert type="warning" showIcon message={`该主机尚未绑定 ${protocol.toUpperCase()} 密码身份，请先通过资产身份接口完成绑定。`} style={{marginBottom: 16}}/>}
        <Space>
          <Button type="primary" loading={loading} onClick={saveEndpoint}>保存端点</Button>
          <Button onClick={() => setEditing(undefined)}>取消</Button>
        </Space>
      </Form>}
    </React.Fragment>}
  </Modal>
}
