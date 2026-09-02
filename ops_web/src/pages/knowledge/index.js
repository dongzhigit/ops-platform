import React, { useEffect, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Divider, Drawer, Empty, Form, Input,
  List, Modal, Popconfirm, Row, Select, Space, Table, Tag, Typography, message,
} from 'antd';
import {
  DeleteOutlined, EditOutlined, HistoryOutlined, PlusOutlined, TeamOutlined,
} from '@ant-design/icons';

import { AuthDiv, Breadcrumb } from 'components';
import { hasPermission, http } from 'libs';


const KIND_LABELS = {
  article: '文档',
  runbook: 'Runbook',
  postmortem: '故障复盘',
  asset: '资产说明',
};

const STATUS_LABELS = {
  draft: '草稿',
  review: '待审核',
  published: '已发布',
  archived: '已归档',
};

const STATUS_COLORS = {
  draft: 'default',
  review: 'gold',
  published: 'green',
  archived: 'blue',
};

const ROLE_LABELS = {
  viewer: '查看者',
  editor: '编辑者',
  manager: '管理员',
};


function SpaceForm({visible, record, onCancel, onSuccess}) {
  const [form] = Form.useForm();

  useEffect(() => {
    if (!visible) return;
    form.setFieldsValue({
      name: record.name,
      slug: record.slug,
      description: record.description,
      visibility: record.visibility || 'private',
      retention_days: record.retention_days || 0,
    });
  }, [visible, record, form]);

  function submit() {
    form.validateFields().then(values => {
      const payload = {...values};
      if (record.id) payload.id = record.id;
      http.post('/api/v1/knowledge/spaces/', payload).then(() => {
        message.success(record.id ? '知识空间已更新' : '知识空间已创建');
        onSuccess();
      });
    });
  }

  return (
    <Modal
      visible={visible}
      title={record.id ? '编辑知识空间' : '新建知识空间'}
      onCancel={onCancel}
      onOk={submit}
      destroyOnClose>
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item name="name" label="空间名称" rules={[{required: true, message: '请输入空间名称'}]}>
          <Input maxLength={100}/>
        </Form.Item>
        <Form.Item
          name="slug"
          label="空间标识"
          extra="仅支持小写字母、数字、下划线和连字符；空间产生文档后不可修改，以保证引用稳定。"
          rules={[
            {required: true, message: '请输入空间标识'},
            {pattern: /^[a-z0-9][a-z0-9_-]{1,63}$/, message: '空间标识格式不正确'},
          ]}>
          <Input maxLength={64}/>
        </Form.Item>
        <Form.Item name="description" label="说明"><Input.TextArea rows={3} maxLength={1000}/></Form.Item>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="visibility" label="可见范围" rules={[{required: true}]}>
              <Select>
                <Select.Option value="private">仅成员可见</Select.Option>
                <Select.Option value="internal">组织内可见</Select.Option>
              </Select>
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="retention_days"
              label="保留天数"
              extra="0 表示永久保留">
              <Input type="number" min={0} max={3650}/>
            </Form.Item>
          </Col>
        </Row>
      </Form>
    </Modal>
  );
}


function MemberManager({visible, space, onCancel}) {
  const [members, setMembers] = useState([]);
  const [users, setUsers] = useState([]);
  const [userId, setUserId] = useState();
  const [role, setRole] = useState('viewer');
  const [loading, setLoading] = useState(false);

  function reload() {
    if (!space.id) return;
    setLoading(true);
    Promise.all([
      http.get(`/api/v1/knowledge/spaces/${space.id}/members/`),
      http.get(`/api/v1/knowledge/spaces/${space.id}/users/`),
    ]).then(([memberData, userData]) => {
      setMembers(memberData);
      setUsers(userData);
    }).finally(() => setLoading(false));
  }

  useEffect(() => {
    if (visible) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, space.id]);

  function saveMember() {
    if (!userId) return message.warning('请选择用户');
    http.post(`/api/v1/knowledge/spaces/${space.id}/members/`, {
      user_id: userId,
      role,
    }).then(() => {
      message.success('成员权限已保存');
      setUserId(undefined);
      reload();
    });
  }

  function removeMember(record) {
    http.delete(`/api/v1/knowledge/spaces/${space.id}/members/`, {
      params: {user_id: record.user.id},
    }).then(() => {
      message.success('成员已移除');
      reload();
    });
  }

  return (
    <Modal visible={visible} width={760} title={`空间成员：${space.name || ''}`}
           footer={null} onCancel={onCancel} destroyOnClose>
      <Space style={{marginBottom: 16}}>
        <Select
          showSearch
          optionFilterProp="children"
          placeholder="选择用户"
          value={userId}
          onChange={setUserId}
          style={{width: 260}}>
          {users.map(item => (
            <Select.Option key={item.id} value={item.id}>
              {item.nickname}（{item.username}）
            </Select.Option>
          ))}
        </Select>
        <Select value={role} onChange={setRole} style={{width: 120}}>
          {Object.keys(ROLE_LABELS).map(key => (
            <Select.Option key={key} value={key}>{ROLE_LABELS[key]}</Select.Option>
          ))}
        </Select>
        <Button type="primary" onClick={saveMember}>添加或更新</Button>
      </Space>
      <Table rowKey="id" loading={loading} dataSource={members} pagination={false}>
        <Table.Column title="成员" render={item => item.user.name}/>
        <Table.Column title="角色" dataIndex="role" render={value => ROLE_LABELS[value]}/>
        <Table.Column title="加入时间" dataIndex="created_at"/>
        <Table.Column title="操作" width={90} render={item => (
          <Popconfirm title="确定移除该成员？" onConfirm={() => removeMember(item)}>
            <Button type="link" danger>移除</Button>
          </Popconfirm>
        )}/>
      </Table>
    </Modal>
  );
}


function DocumentForm({visible, record, spaces, onCancel, onSuccess}) {
  const [form] = Form.useForm();
  const canPublish = hasPermission('knowledge.document.publish');

  useEffect(() => {
    if (!visible) return;
    form.setFieldsValue({
      space_id: record.space ? record.space.id : record.space_id,
      title: record.title,
      slug: record.slug,
      kind: record.kind || 'article',
      status: record.status || 'draft',
      summary: record.summary,
      tags: record.tags || [],
      content: record.content || '',
      change_note: '',
    });
  }, [visible, record, form]);

  function submit() {
    form.validateFields().then(values => {
      if (record.id) {
        const payload = {...values, id: record.id};
        delete payload.space_id;
        http.patch('/api/v1/knowledge/documents/', payload).then(() => {
          message.success('文档已保存并生成新版本');
          onSuccess();
        });
      } else {
        http.post('/api/v1/knowledge/documents/', values).then(() => {
          message.success('文档已创建');
          onSuccess();
        });
      }
    });
  }

  const statusOptions = Object.keys(STATUS_LABELS).filter(
    key => canPublish || key !== 'published' || record.status === 'published'
  );
  return (
    <Modal
      visible={visible}
      width={1000}
      title={record.id ? `编辑文档 v${record.version}` : '新建文档'}
      onCancel={onCancel}
      onOk={submit}
      destroyOnClose>
      <Form form={form} layout="vertical" preserve={false}>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="space_id" label="知识空间" rules={[{required: true, message: '请选择空间'}]}>
              <Select disabled={Boolean(record.id)}>
                {spaces.filter(item => item.can_edit).map(item => (
                  <Select.Option key={item.id} value={item.id}>{item.name}</Select.Option>
                ))}
              </Select>
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="title" label="标题" rules={[{required: true, message: '请输入标题'}]}>
              <Input maxLength={200}/>
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item name="kind" label="类型" rules={[{required: true}]}>
              <Select>{Object.keys(KIND_LABELS).map(key => (
                <Select.Option key={key} value={key}>{KIND_LABELS[key]}</Select.Option>
              ))}</Select>
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="status" label="状态" rules={[{required: true}]}>
              <Select>{statusOptions.map(key => (
                <Select.Option key={key} value={key}>{STATUS_LABELS[key]}</Select.Option>
              ))}</Select>
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="slug" label="文档标识" extra="留空时根据标题自动生成">
              <Input maxLength={100}/>
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="summary" label="摘要"><Input.TextArea rows={2} maxLength={1000}/></Form.Item>
        <Form.Item name="tags" label="标签"><Select mode="tags" tokenSeparators={[',']} maxTagCount={12}/></Form.Item>
        <Form.Item
          name="content"
          label="正文"
          extra="支持 Markdown 文本存储；当前预览按纯文本显示，不执行 HTML 或脚本。"
          rules={[{required: true, message: '请输入正文'}]}>
          <Input.TextArea rows={18} showCount maxLength={1048576}/>
        </Form.Item>
        <Form.Item name="change_note" label="版本说明"><Input maxLength={255}/></Form.Item>
      </Form>
    </Modal>
  );
}


function DocumentDetail({visible, document, onClose, onEdit, onChanged}) {
  const [revisions, setRevisions] = useState([]);
  const [historyVisible, setHistoryVisible] = useState(false);

  function loadRevisions() {
    if (!document.id) return;
    http.get(`/api/v1/knowledge/documents/${document.id}/revisions/`).then(setRevisions);
  }

  useEffect(() => {
    if (visible && document.id) loadRevisions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, document.id, document.version]);

  function publish() {
    http.patch('/api/v1/knowledge/documents/', {
      id: document.id,
      status: 'published',
      change_note: '发布文档',
    }).then(() => {
      message.success('文档已发布');
      onChanged();
    });
  }

  function restore(version) {
    Modal.confirm({
      title: `恢复版本 v${version}`,
      content: '恢复会创建一个新版本，不会覆盖或删除现有历史。',
      onOk: () => http.post(`/api/v1/knowledge/documents/${document.id}/revisions/`, {
        version,
        change_note: `从 v${version} 恢复`,
      }).then(() => {
        message.success('版本已恢复');
        setHistoryVisible(false);
        onChanged();
      }),
    });
  }

  function remove() {
    Modal.confirm({
      title: '删除文档',
      content: '文档会被软删除并保留审计记录，普通检索将不再返回该文档。',
      onOk: () => http.delete('/api/v1/knowledge/documents/', {
        params: {id: document.id},
      }).then(() => {
        message.success('文档已删除');
        onClose();
        onChanged();
      }),
    });
  }

  return (
    <>
      <Drawer
        visible={visible}
        width={860}
        title={document.title}
        onClose={onClose}
        extra={<Space>
          <Button icon={<HistoryOutlined/>} onClick={() => setHistoryVisible(true)}>版本</Button>
          {document.can_edit && (document.status !== 'published' || document.can_publish) && (
            <Button icon={<EditOutlined/>} onClick={onEdit}>编辑</Button>
          )}
          {document.can_publish && document.status !== 'published' && (
            <Button type="primary" onClick={publish}>发布</Button>
          )}
          {document.can_delete && <Button danger icon={<DeleteOutlined/>} onClick={remove}>删除</Button>}
        </Space>}>
        <Descriptions size="small" bordered column={2}>
          <Descriptions.Item label="空间">{document.space && document.space.name}</Descriptions.Item>
          <Descriptions.Item label="版本">v{document.version}</Descriptions.Item>
          <Descriptions.Item label="类型">{KIND_LABELS[document.kind]}</Descriptions.Item>
          <Descriptions.Item label="状态"><Tag color={STATUS_COLORS[document.status]}>{STATUS_LABELS[document.status]}</Tag></Descriptions.Item>
          <Descriptions.Item label="更新人">{document.updated_by && document.updated_by.name}</Descriptions.Item>
          <Descriptions.Item label="更新时间">{document.updated_at}</Descriptions.Item>
          <Descriptions.Item label="引用" span={2}>
            <Typography.Text copyable>{document.citation}</Typography.Text>
          </Descriptions.Item>
        </Descriptions>
        {document.tags && document.tags.length > 0 && (
          <div style={{marginTop: 16}}>{document.tags.map(tag => <Tag key={tag}>{tag}</Tag>)}</div>
        )}
        {document.summary && <Alert style={{marginTop: 16}} message={document.summary} type="info"/>}
        <Divider orientation="left">正文</Divider>
        <pre style={{whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'inherit'}}>
          {document.content}
        </pre>
      </Drawer>
      <Modal visible={historyVisible} width={820} title="文档版本"
             footer={null} onCancel={() => setHistoryVisible(false)}>
        <Table rowKey="id" dataSource={revisions} pagination={false}>
          <Table.Column title="版本" dataIndex="version" width={80} render={value => `v${value}`}/>
          <Table.Column title="状态" dataIndex="status" width={100} render={value => STATUS_LABELS[value]}/>
          <Table.Column title="说明" dataIndex="change_note"/>
          <Table.Column title="更新人" render={item => item.created_by.name} width={110}/>
          <Table.Column title="时间" dataIndex="created_at" width={165}/>
          <Table.Column title="操作" width={90} render={item => (
            document.can_edit && item.version !== document.version ? (
              <Button type="link" onClick={() => restore(item.version)}>恢复</Button>
            ) : null
          )}/>
        </Table>
      </Modal>
    </>
  );
}


export default function KnowledgeIndex() {
  const [spaces, setSpaces] = useState([]);
  const [selectedSpaceId, setSelectedSpaceId] = useState();
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [spaceForm, setSpaceForm] = useState(null);
  const [memberSpace, setMemberSpace] = useState(null);
  const [documentForm, setDocumentForm] = useState(null);
  const [detail, setDetail] = useState(null);

  const selectedSpace = spaces.find(item => item.id === selectedSpaceId);

  function loadSpaces(preferredId) {
    return http.get('/api/v1/knowledge/spaces/').then(data => {
      setSpaces(data);
      const target = preferredId || selectedSpaceId;
      if (target && data.some(item => item.id === target)) setSelectedSpaceId(target);
      else if (data.length) setSelectedSpaceId(data[0].id);
      else setSelectedSpaceId(undefined);
    });
  }

  function loadDocuments() {
    if (!selectedSpaceId) {
      setDocuments([]);
      return Promise.resolve();
    }
    setLoading(true);
    const params = {space_id: selectedSpaceId};
    if (query.trim()) params.q = query.trim();
    return http.get('/api/v1/knowledge/documents/', {params})
      .then(setDocuments)
      .finally(() => setLoading(false));
  }

  function openDocument(record) {
    http.get('/api/v1/knowledge/documents/', {params: {id: record.id}}).then(setDetail);
  }

  function refreshDocument() {
    loadDocuments();
    if (detail && detail.id) openDocument(detail);
  }

  useEffect(() => { loadSpaces(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadDocuments(); }, [selectedSpaceId]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AuthDiv auth="knowledge.space.view|knowledge.document.view">
      <Breadcrumb>
        <Breadcrumb.Item>首页</Breadcrumb.Item>
        <Breadcrumb.Item>知识库</Breadcrumb.Item>
      </Breadcrumb>
      <Alert
        showIcon
        type="info"
        style={{marginBottom: 16}}
        message="知识内容按空间成员角色和页面权限双重过滤；AI 只能引用已发布且当前用户有权访问的版本。"/>
      <Row gutter={16}>
        <Col xs={24} lg={7} xl={6}>
          <Card
            title="知识空间"
            extra={hasPermission('knowledge.space.manage') ? (
              <Button type="link" icon={<PlusOutlined/>} onClick={() => setSpaceForm({})}>新建</Button>
            ) : null}>
            <List
              dataSource={spaces}
              locale={{emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可访问空间"/>}}
              renderItem={item => (
                <List.Item
                  style={{cursor: 'pointer', background: item.id === selectedSpaceId ? '#f0f5ff' : undefined, padding: 12}}
                  onClick={() => setSelectedSpaceId(item.id)}
                  actions={item.can_manage ? [
                    <Button key="members" type="text" size="small" icon={<TeamOutlined/>}
                            onClick={event => {event.stopPropagation(); setMemberSpace(item);}}/>,
                    <Button key="edit" type="text" size="small" icon={<EditOutlined/>}
                            onClick={event => {event.stopPropagation(); setSpaceForm(item);}}/>,
                  ] : []}>
                  <List.Item.Meta
                    title={<Space>{item.name}<Tag>{ROLE_LABELS[item.role]}</Tag></Space>}
                    description={`${item.document_count} 篇文档 · ${item.visibility === 'private' ? '仅成员' : '组织内'}`}/>
                </List.Item>
              )}/>
          </Card>
        </Col>
        <Col xs={24} lg={17} xl={18}>
          <Card
            title={selectedSpace ? selectedSpace.name : '文档'}
            extra={<Space>
              <Input.Search
                allowClear
                value={query}
                onChange={event => setQuery(event.target.value)}
                onSearch={loadDocuments}
                placeholder="检索标题、正文和标签"
                style={{width: 260}}/>
              {selectedSpace && selectedSpace.can_edit && hasPermission('knowledge.document.edit') && (
                <Button type="primary" icon={<PlusOutlined/>}
                        onClick={() => setDocumentForm({space_id: selectedSpace.id})}>新建文档</Button>
              )}
            </Space>}>
            <List
              loading={loading}
              dataSource={documents}
              locale={{emptyText: <Empty description={selectedSpace ? '暂无文档' : '请先创建或选择空间'}/>}}
              renderItem={item => (
                <List.Item onClick={() => openDocument(item)} style={{cursor: 'pointer'}}>
                  <List.Item.Meta
                    title={<Space>
                      <Typography.Link>{item.title}</Typography.Link>
                      <Tag>{KIND_LABELS[item.kind]}</Tag>
                      <Tag color={STATUS_COLORS[item.status]}>{STATUS_LABELS[item.status]}</Tag>
                    </Space>}
                    description={(
                      <div>
                        <div>{item.summary || '暂无摘要'}</div>
                        <div style={{marginTop: 6, color: '#999'}}>
                          v{item.version} · {item.updated_by.name} · {item.updated_at}
                        </div>
                      </div>
                    )}/>
                  <div>{item.tags.map(tag => <Tag key={tag}>{tag}</Tag>)}</div>
                </List.Item>
              )}/>
          </Card>
        </Col>
      </Row>
      <SpaceForm
        visible={Boolean(spaceForm)}
        record={spaceForm || {}}
        onCancel={() => setSpaceForm(null)}
        onSuccess={() => {
          const id = spaceForm && spaceForm.id;
          setSpaceForm(null);
          loadSpaces(id);
        }}/>
      <MemberManager
        visible={Boolean(memberSpace)}
        space={memberSpace || {}}
        onCancel={() => setMemberSpace(null)}/>
      <DocumentForm
        visible={Boolean(documentForm)}
        record={documentForm || {}}
        spaces={spaces}
        onCancel={() => setDocumentForm(null)}
        onSuccess={() => {
          setDocumentForm(null);
          refreshDocument();
          loadSpaces(selectedSpaceId);
        }}/>
      <DocumentDetail
        visible={Boolean(detail)}
        document={detail || {}}
        onClose={() => setDetail(null)}
        onEdit={() => setDocumentForm(detail)}
        onChanged={refreshDocument}/>
    </AuthDiv>
  );
}
