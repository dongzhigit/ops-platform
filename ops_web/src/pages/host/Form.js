/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React, { useState, useEffect } from 'react';
import { observer } from 'mobx-react';
import { ExclamationCircleOutlined, UploadOutlined } from '@ant-design/icons';
import { Modal, Form, Input, TreeSelect, Button, Upload, Alert, Checkbox, Table, Tag, message } from 'antd';
import { hasPermission, http, X_TOKEN } from 'libs';
import store from './store';
import styles from './index.module.less';


function DiscoveryPreview({result, onSelectionChange}) {
  const recommendations = result && result.recommendations ? result.recommendations : {};
  const services = recommendations.services || [];
  const connections = recommendations.connections || [];
  const [selectedServiceKeys, setSelectedServiceKeys] = useState(
    services.filter(item => item.selected !== false).map(item => item.key)
  );
  const [selectedConnectionKeys, setSelectedConnectionKeys] = useState(
    connections.filter(item => item.selected !== false).map(item => item.key)
  );
  function updateServices(keys) {
    setSelectedServiceKeys(keys);
    if (onSelectionChange) onSelectionChange(keys, selectedConnectionKeys);
  }
  function updateConnections(keys) {
    setSelectedConnectionKeys(keys);
    if (onSelectionChange) onSelectionChange(selectedServiceKeys, keys);
  }
  return (
    <div>
      <Alert
        showIcon
        type="info"
        style={{marginBottom: 12}}
        message={`默认选择 ${services.length} 个服务、${connections.length} 条业务连接，确认后生成拓扑，之后可在拓扑页人工补充。`}/>
      <Table
        size="small"
        rowKey="key"
        dataSource={services}
        rowSelection={{
          selectedRowKeys: selectedServiceKeys,
          onChange: updateServices,
        }}
        pagination={false}
        locale={{emptyText: '未发现候选服务'}}
        style={{marginBottom: 12}}>
        <Table.Column title="服务" dataIndex="name"/>
        <Table.Column title="层级" dataIndex="runtime_layer" render={value => <Tag>{value || 'unknown'}</Tag>}/>
        <Table.Column title="进程" dataIndex="processes" render={values => (values || []).join(', ') || '-'}/>
      </Table>
      <Table
        size="small"
        rowKey="key"
        dataSource={connections}
        rowSelection={{
          selectedRowKeys: selectedConnectionKeys,
          onChange: updateConnections,
        }}
        pagination={false}
        locale={{emptyText: '未发现候选连接'}}>
        <Table.Column title="来源" dataIndex="source"/>
        <Table.Column title="目标" dataIndex="target"/>
        <Table.Column title="类型" dataIndex="service"/>
        <Table.Column title="层级" dataIndex="runtime_layer" render={value => <Tag>{value || 'unknown'}</Tag>}/>
        <Table.Column title="连接数" dataIndex="count"/>
      </Table>
    </div>
  );
}


export default observer(function () {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [fileList, setFileList] = useState([]);

  useEffect(() => {
    if (store.record.has_pkey) {
      const name = store.record.credential_source === 'identity' ? '已绑定的托管身份' : '已配置的主机密钥';
      setFileList([{uid: '0', name}])
    }
  }, [])

  function handleSubmit() {
    setLoading(true);
    const formData = form.getFieldsValue();
    const autoTopologyScan = Boolean(formData.auto_topology_scan);
    delete formData.auto_topology_scan;
    formData['id'] = store.record.id;
    const file = fileList[0];
    if (file && file.data) formData['pkey'] = file.data;
    formData['clear_pkey'] = Boolean(store.record.credential_source === 'host' && !file);
    http.post('/api/host/', formData)
      .then(res => {
        if (res === 'auth fail') {
          setLoading(false)
          promptPassword(formData, autoTopologyScan)
        } else {
          message.success('验证成功');
          store.formVisible = false;
          store.fetchRecords();
          store.fetchExtend(res.id)
          setLoading(false);
          autoDiscoverTopology(res.id, autoTopologyScan);
        }
      }, error => {
        setLoading(false);
        if (shouldPromptPassword(error)) {
          promptPassword(formData, autoTopologyScan)
        }
      })
  }

  function autoDiscoverTopology(hostId, enabled) {
    if (!enabled || !hasPermission('topology.topology.manage')) return;
    message.loading('正在发现业务拓扑...', 1);
    http.post('/api/v1/topology/runtime-scan/', {
      host_ids: [hostId],
      dry_run: true,
    }, {timeout: 125000}).then(data => {
      const result = data && data[0] ? data[0] : {};
      const recommendations = result.recommendations || {};
      const services = recommendations.services || [];
      const connections = recommendations.connections || [];
      if (!services.length && !connections.length) {
        message.info('未发现可自动生成的业务拓扑');
        return null;
      }
      let selectedServiceKeys = services.filter(item => item.selected !== false).map(item => item.key);
      let selectedConnectionKeys = connections.filter(item => item.selected !== false).map(item => item.key);
      Modal.confirm({
        width: 820,
        title: '确认生成业务拓扑',
        okText: '确认生成',
        cancelText: '暂不生成',
        content: (
          <DiscoveryPreview
            result={result}
            onSelectionChange={(serviceKeys, connectionKeys) => {
              selectedServiceKeys = serviceKeys;
              selectedConnectionKeys = connectionKeys;
            }}/>
        ),
        onOk: () => http.post('/api/v1/topology/runtime-scan/', {
          host_ids: [hostId],
          selected_service_keys: selectedServiceKeys,
          selected_connection_keys: selectedConnectionKeys,
        }, {timeout: 125000}).then(() => {
          message.success('业务拓扑已生成');
        }),
      });
      return null;
    }).catch(error => {
      message.warning(`业务拓扑自动发现失败：${error}`);
    });
  }

  function shouldPromptPassword(error) {
    return !hasManagedIdentity && !formDataHasKey() && typeof error === 'string' && (
      error.indexOf('认证失败') !== -1 || error.indexOf('auth fail') !== -1
    )
  }

  function formDataHasKey() {
    const file = fileList[0];
    return Boolean(file && file.data)
  }

  function promptPassword(formData, autoTopologyScan) {
    if (formData.pkey) {
      message.error('独立密钥认证失败')
    } else {
      const onChange = v => formData.password = v;
      Modal.confirm({
        icon: <ExclamationCircleOutlined/>,
        title: '请输入主机登录密码',
        content: <ConfirmForm username={formData.username} onChange={onChange}/>,
        onOk: () => handleConfirm(formData, autoTopologyScan),
      })
    }
  }

  function handleConfirm(formData, autoTopologyScan) {
    if (formData.password) {
      return http.post('/api/host/', formData)
        .then(res => {
          message.success('验证成功');
          store.formVisible = false;
          store.fetchRecords();
          store.fetchExtend(res.id)
          setLoading(false);
          autoDiscoverTopology(res.id, autoTopologyScan);
        })
    }
    message.error('请输入授权密码')
  }

  const ConfirmForm = (props) => (
    <Form layout="vertical" style={{marginTop: 24}}>
      <Form.Item required label="授权密码" extra={`用户 ${props.username} 的密码， 该密码仅做首次验证使用，不会存储该密码。`}>
        <Input.Password onChange={e => props.onChange(e.target.value)}/>
      </Form.Item>
    </Form>
  )

  function handleUploadChange(v) {
    if (v.fileList.length === 0) {
      setFileList([])
    }
  }

  function handleUpload(file, fileList) {
    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    http.post('/api/host/parse/', formData)
      .then(res => {
        file.data = res;
        setFileList([file])
      })
      .finally(() => setUploading(false))
    return false
  }

  const info = store.record;
  const hasManagedIdentity = store.record.credential_source === 'identity';
  const canAutoScanTopology = !info.id && hasPermission('topology.topology.manage');
  return (
    <Modal
      visible
      width={700}
      maskClosable={false}
      title={store.record.id ? '编辑主机' : '新建主机'}
      okText="验证"
      onCancel={() => store.formVisible = false}
      confirmLoading={loading}
      onOk={handleSubmit}>
      <Form
        form={form}
        labelCol={{span: 5}}
        wrapperCol={{span: 17}}
        initialValues={{auto_topology_scan: canAutoScanTopology, ...info}}>
        <Form.Item required name="group_ids" label="主机分组">
          <TreeSelect
            multiple
            treeNodeLabelProp="name"
            treeData={store.treeData}
            showCheckedStrategy={TreeSelect.SHOW_CHILD}
            placeholder="请选择分组"/>
        </Form.Item>
        <Form.Item required name="name" label="主机名称">
          <Input placeholder="请输入主机名称"/>
        </Form.Item>
        <Form.Item required label="连接地址" style={{marginBottom: 0}}>
          <Form.Item name="username" className={styles.formAddress1} style={{width: 'calc(30%)'}}>
            <Input addonBefore="ssh" placeholder="用户名"/>
          </Form.Item>
          <Form.Item name="hostname" className={styles.formAddress2} style={{width: 'calc(40%)'}}>
            <Input addonBefore="@" placeholder="主机名/IP"/>
          </Form.Item>
          <Form.Item name="port" className={styles.formAddress3} style={{width: 'calc(30%)'}}>
            <Input addonBefore="-p" placeholder="端口"/>
          </Form.Item>
        </Form.Item>
        <Form.Item label="主机凭据" extra={hasManagedIdentity
          ? '该主机正在使用托管身份，请在资产凭据中心修改或解绑。'
          : '密钥内容不会返回浏览器；可在资产凭据中心统一绑定，或在此替换独立私钥。'}>
          <Upload name="file" fileList={fileList} headers={{'X-Token': X_TOKEN}} beforeUpload={handleUpload}
                  disabled={hasManagedIdentity}
                  showUploadList={{showRemoveIcon: !hasManagedIdentity}}
                  onChange={handleUploadChange}>
            {fileList.length === 0 && !hasManagedIdentity
              ? <Button loading={uploading} icon={<UploadOutlined/>}>点击上传</Button>
              : null}
          </Upload>
        </Form.Item>
        <Form.Item name="desc" label="备注信息">
          <Input.TextArea placeholder="请输入主机备注信息"/>
        </Form.Item>
        {canAutoScanTopology && (
          <Form.Item name="auto_topology_scan" valuePropName="checked" wrapperCol={{span: 17, offset: 5}}>
            <Checkbox>验证后自动发现业务拓扑</Checkbox>
          </Form.Item>
        )}
        <Form.Item wrapperCol={{span: 17, offset: 5}}>
          <Alert showIcon type="info" message="首次验证时需要输入登录用户名对应的密码，该密码会用于配置SSH密钥认证，不会存储该密码。"/>
        </Form.Item>
      </Form>
    </Modal>
  )
})
