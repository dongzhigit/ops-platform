import React, { useEffect, useState } from 'react';
import {
  Alert, Button, Card, Checkbox, Col, Collapse, Descriptions, Divider, Drawer,
  Empty, Form, Input, InputNumber, List, Modal, Row, Select, Space, Switch,
  Table, Tag, Timeline, Typography, message,
} from 'antd';
import {
  EyeOutlined, RobotOutlined, SafetyCertificateOutlined, SearchOutlined,
  SettingOutlined,
} from '@ant-design/icons';

import { AuthDiv, Breadcrumb } from 'components';
import { hasPermission, http } from 'libs';


const STATUS_LABELS = {running: '调查中', completed: '已完成', failed: '失败'};
const STATUS_COLORS = {running: 'processing', completed: 'green', failed: 'red'};
const RISK_LABELS = {low: '低风险', medium: '中风险', high: '高风险', critical: '严重风险'};
const RISK_COLORS = {low: 'green', medium: 'blue', high: 'orange', critical: 'red'};
const CONFIDENCE_LABELS = {low: '低', medium: '中', high: '高'};
const REMEDIATION_STATUS_LABELS = {
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
const REMEDIATION_STATUS_COLORS = {
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
const EXECUTION_STATUS_LABELS = {running: '执行登记中', succeeded: '已验证成功', failed: '验证失败', rolled_back: '已回滚'};
const EXECUTION_STATUS_COLORS = {running: 'processing', succeeded: 'green', failed: 'red', rolled_back: 'purple'};
const PLATFORM_ACTION_LABELS = {
  'exec.run': '批量执行',
  'deploy.run': '发布执行',
  'schedule.run': '计划任务',
  'file.distribute': '文件分发',
  'config.write': '配置写入',
};


function ModelConfigModal({visible, config, onCancel, onSuccess}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [apiFormat, setApiFormat] = useState('openai');

  useEffect(() => {
    if (!visible) return;
    const nextApiFormat = config.api_format || 'openai';
    setApiFormat(nextApiFormat);
    form.setFieldsValue({
      enabled: config.enabled,
      api_format: nextApiFormat,
      base_url: config.base_url,
      model: config.model,
      api_key: '',
      clear_api_key: false,
      json_mode: config.json_mode,
      request_timeout: config.request_timeout,
      max_hosts: config.max_hosts,
      knowledge_limit: config.knowledge_limit,
      max_output_tokens: config.max_output_tokens,
      max_response_bytes: config.max_response_bytes,
      rate_limit_per_minute: config.rate_limit_per_minute,
    });
  }, [visible, config, form]);

  function submit() {
    form.validateFields().then(values => {
      if (values.enabled && !config.api_key_configured && !values.api_key) {
        message.error('启用 AI 运维前必须填写 API Key');
        return;
      }
      setSaving(true);
      http.post('/api/v1/aiops/config/', values).then(data => {
        message.success('模型配置已加密保存并立即生效');
        onSuccess(data);
      }).finally(() => setSaving(false));
    });
  }

  return (
    <Modal
      visible={visible}
      width={820}
      title="AI 模型配置"
      confirmLoading={saving}
      onOk={submit}
      onCancel={onCancel}
      destroyOnClose>
      <Alert
        showIcon
        type="warning"
        message="API Key 只会提交一次并以 AES-GCM 密文保存，页面和查询接口不会返回明文。"
        description={`当前密钥：${config.api_key_configured ? '已配置' : '未配置'}；来源：${config.api_key_source === 'database' ? '页面加密配置' : config.api_key_source === 'environment' ? '环境密钥文件' : '无'}`}
        style={{marginBottom: 16}}/>
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item name="enabled" label="启用只读 AI 调查" valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="关闭"/>
        </Form.Item>
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item
              name="api_format"
              label="API 格式"
              rules={[{required: true, message: '请选择 API 格式'}]}>
              <Select onChange={setApiFormat}>
                <Select.Option value="openai">OpenAI Chat Completions</Select.Option>
                <Select.Option value="anthropic">Anthropic Messages</Select.Option>
              </Select>
            </Form.Item>
          </Col>
          <Col span={16}>
            <Form.Item
              name="base_url"
              label="API 根地址"
              extra={`填写包含 /v1 的根地址，系统将自动追加 ${apiFormat === 'anthropic' ? '/messages' : '/chat/completions'}；公网地址必须使用 HTTPS，回环、Docker 内部地址和内网 IP 可使用 HTTP。`}
              rules={[
                {required: true, message: '请输入模型服务地址'},
                {max: 500, message: '地址不能超过 500 个字符'},
                {type: 'url', message: '请输入完整的 HTTP(S) URL'},
              ]}>
              <Input placeholder={apiFormat === 'anthropic'
                ? 'https://api.anthropic.com/v1'
                : 'https://api.openai.com/v1'}/>
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="model" label="模型名称" rules={[{max: 100}]}>
              <Input placeholder="例如 ops-model-v1"/>
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="api_key"
              label="API Key"
              extra={config.api_key_configured ? '留空表示保留现有密钥。' : '启用前必须填写。'}>
              <Input.Password autoComplete="new-password" maxLength={8192}
                              placeholder={config.api_key_configured ? '已配置，留空不修改' : '请输入 API Key'}/>
            </Form.Item>
          </Col>
        </Row>
        {config.api_key_configured && config.api_key_source === 'database' && (
          <Form.Item name="clear_api_key" valuePropName="checked">
            <Checkbox>清除页面保存的 API Key（启用状态下不能清除唯一可用密钥）</Checkbox>
          </Form.Item>
        )}
        <Divider orientation="left">调用与配额限制</Divider>
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item name="request_timeout" label="请求超时（秒）" rules={[{required: true}]}>
              <InputNumber min={1} max={120} style={{width: '100%'}}/>
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="max_hosts" label="单次主机上限" rules={[{required: true}]}>
              <InputNumber min={1} max={100} style={{width: '100%'}}/>
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="knowledge_limit" label="知识片段上限" rules={[{required: true}]}>
              <InputNumber min={1} max={20} style={{width: '100%'}}/>
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="max_output_tokens" label="最大输出 Token" rules={[{required: true}]}>
              <InputNumber min={256} max={8192} style={{width: '100%'}}/>
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="max_response_bytes" label="响应字节上限" rules={[{required: true}]}>
              <InputNumber min={4096} max={4194304} style={{width: '100%'}}/>
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="rate_limit_per_minute" label="每分钟调用上限" rules={[{required: true}]}>
              <InputNumber min={1} max={60} style={{width: '100%'}}/>
            </Form.Item>
          </Col>
        </Row>
        <Form.Item
          name="json_mode"
          label="请求 JSON Object 模式（仅 OpenAI）"
          extra={apiFormat === 'anthropic' ? 'Anthropic Messages 没有该参数，系统仍会通过提示词和后端校验强制要求 JSON。' : null}
          valuePropName="checked">
          <Switch disabled={apiFormat === 'anthropic'} checkedChildren="启用" unCheckedChildren="兼容模式"/>
        </Form.Item>
      </Form>
    </Modal>
  );
}


function CitationTags({values}) {
  if (!values || !values.length) return <Typography.Text type="secondary">无引用</Typography.Text>;
  return <>{values.map(value => <Tag key={value} color="blue">{value}</Tag>)}</>;
}


function RemediationProposalModal({visible, plan, actions, onCancel, onSuccess}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!visible || !plan) return;
    const targets = [];
    const validations = [];
    const rollbacks = [];
    (plan.steps || []).forEach(step => {
      (step.targets || []).forEach(item => {
        if (item && !targets.includes(item)) targets.push(item);
      });
      if (step.validation) validations.push(step.validation);
      if (step.rollback) rollbacks.push(step.rollback);
    });
    form.setFieldsValue({
      proposed_action: 'manual_check',
      title: plan.title,
      summary: plan.summary,
      target_refs: targets,
      validation_plan: validations.join('\n'),
      rollback_plan: plan.rollback_plan || rollbacks.join('\n'),
    });
  }, [visible, plan, form]);

  function submit() {
    form.validateFields().then(values => {
      setSaving(true);
      return http.post('/api/v1/aiops/remediations/', {
        action_plan_id: plan.id,
        ...values,
      });
    }).then(data => {
      message.success('修复提案已创建');
      onSuccess(data);
    }).finally(() => setSaving(false));
  }

  return (
    <Modal
      visible={visible}
      width={760}
      title="受控修复提案"
      confirmLoading={saving}
      onOk={submit}
      onCancel={onCancel}
      destroyOnClose>
      <Alert
        showIcon
        type="warning"
        message="提案只会提交审批，不会执行命令、脚本、发布、配置写入或流量切换。"
        style={{marginBottom: 16}}/>
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="proposed_action"
          label="白名单动作"
          rules={[{required: true, message: '请选择白名单动作'}]}>
          <Select>
            {(actions || []).map(item => (
              <Select.Option key={item.value} value={item.value}>
                {item.label} / {RISK_LABELS[item.risk_level]}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
        <Form.Item name="title" label="标题" rules={[{required: true}, {max: 200}]}>
          <Input/>
        </Form.Item>
        <Form.Item name="summary" label="提案说明" rules={[{required: true}, {max: 4000}]}>
          <Input.TextArea rows={4} maxLength={4000} showCount/>
        </Form.Item>
        <Form.Item name="target_refs" label="目标引用" rules={[{required: true}]}>
          <Select mode="tags" tokenSeparators={[',', '，']} maxTagCount={6}/>
        </Form.Item>
        <Form.Item name="validation_plan" label="验证方案" rules={[{required: true}, {max: 4000}]}>
          <Input.TextArea rows={3} maxLength={4000} showCount/>
        </Form.Item>
        <Form.Item name="rollback_plan" label="回滚方案" rules={[{max: 4000}]}>
          <Input.TextArea rows={3} maxLength={4000} showCount/>
        </Form.Item>
      </Form>
    </Modal>
  );
}


function RemediationExecutionModal({visible, proposal, record, onCancel, onSuccess}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [references, setReferences] = useState([]);
  const [loadingRefs, setLoadingRefs] = useState(false);
  const [platformAction, setPlatformAction] = useState();
  const isUpdate = Boolean(record && record.id);

  function loadReferences(action) {
    setReferences([]);
    if (!action) return;
    setLoadingRefs(true);
    http.get('/api/v1/aiops/remediations/platform-references/', {
      params: {platform_action: action},
    }).then(setReferences).finally(() => setLoadingRefs(false));
  }

  useEffect(() => {
    if (!visible) return;
    if (isUpdate) {
      form.setFieldsValue({
        status: 'succeeded',
        validation_result: record.validation_result || '',
        rollback_result: record.rollback_result || '',
      });
    } else {
      setPlatformAction(undefined);
      setReferences([]);
      form.setFieldsValue({
        mode: 'manual_record',
        platform_action: undefined,
        execution_ref: '',
        execution_summary: '',
      });
    }
  }, [visible, isUpdate, record, form]);

  function submit() {
    form.validateFields().then(values => {
      setSaving(true);
      const payload = isUpdate ? {id: record.id, ...values} : {
        proposal_id: proposal.id,
        ...values,
      };
      return http.post('/api/v1/aiops/remediations/executions/', payload);
    }).then(() => {
      message.success(isUpdate ? '执行结果已记录' : '执行登记已创建');
      onSuccess();
    }).finally(() => setSaving(false));
  }

  return (
    <Modal
      visible={visible}
      width={720}
      title={isUpdate ? '记录验证/回滚结果' : '登记受控执行证据'}
      confirmLoading={saving}
      onOk={submit}
      onCancel={onCancel}
      destroyOnClose>
      <Alert
        showIcon
        type="warning"
        message="这里仅记录审批后的执行证据，不会调用 SSH、发布、配置写入或流量切换。"
        style={{marginBottom: 16}}/>
      <Form form={form} layout="vertical" preserve={false}>
        {!isUpdate ? (
          <>
            <Form.Item name="mode" label="登记模式" rules={[{required: true}]}>
              <Select>
                <Select.Option value="manual_record">人工执行记录</Select.Option>
                <Select.Option value="platform_reference">平台执行引用</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item noStyle shouldUpdate={(prev, next) => prev.mode !== next.mode}>
              {({getFieldValue}) => (
                <Form.Item
                  name="platform_action"
                  label="平台动作"
                  rules={[{
                    required: getFieldValue('mode') === 'platform_reference',
                    message: '请选择平台动作',
                  }]}>
                  <Select
                    allowClear
                    onChange={value => {
                      setPlatformAction(value);
                      form.setFieldsValue({execution_ref: undefined});
                      loadReferences(value);
                    }}>
                    {Object.entries(PLATFORM_ACTION_LABELS).map(([key, value]) => (
                      <Select.Option key={key} value={key}>{value}</Select.Option>
                    ))}
                  </Select>
                </Form.Item>
              )}
            </Form.Item>
            <Form.Item noStyle shouldUpdate={(prev, next) => (
              prev.mode !== next.mode || prev.platform_action !== next.platform_action
            )}>
              {({getFieldValue}) => (
                <Form.Item
                  name="execution_ref"
                  label="执行引用"
                  rules={[{
                    required: getFieldValue('mode') === 'platform_reference',
                    message: '请选择执行引用',
                  }]}>
                  <Select
                    showSearch
                    allowClear
                    loading={loadingRefs}
                    disabled={!platformAction}
                    placeholder="选择已存在的平台执行记录"
                    notFoundContent={platformAction ? '暂无可见记录' : '请先选择平台动作'}>
                    {references.map(item => (
                      <Select.Option key={`${item.platform_action}:${item.ref}`} value={item.ref}>
                        {item.label}
                      </Select.Option>
                    ))}
                  </Select>
                </Form.Item>
              )}
            </Form.Item>
            <Form.Item name="execution_summary" label="执行摘要" rules={[{required: true}, {max: 4000}]}>
              <Input.TextArea rows={4} maxLength={4000} showCount/>
            </Form.Item>
          </>
        ) : (
          <>
            <Form.Item name="status" label="结果状态" rules={[{required: true}]}>
              <Select>
                <Select.Option value="succeeded">已验证成功</Select.Option>
                <Select.Option value="failed">验证失败</Select.Option>
                <Select.Option value="rolled_back">已回滚</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item name="validation_result" label="验证结果">
              <Input.TextArea rows={4} maxLength={4000} showCount/>
            </Form.Item>
            <Form.Item name="rollback_result" label="回滚结果">
              <Input.TextArea rows={4} maxLength={4000} showCount/>
            </Form.Item>
          </>
        )}
      </Form>
    </Modal>
  );
}


function InvestigationDetail({visible, record, onClose}) {
  const result = record.result || {};
  const plan = record.action_plan;
  const [remediations, setRemediations] = useState([]);
  const [remediationActions, setRemediationActions] = useState([]);
  const [remediationVisible, setRemediationVisible] = useState(false);
  const [executionTarget, setExecutionTarget] = useState(null);
  const [executionRecord, setExecutionRecord] = useState(null);
  const [submittingProposal, setSubmittingProposal] = useState(false);
  const canViewRemediation = hasPermission('aiops.remediation.view') || hasPermission('aiops.remediation.manage');
  const canManageRemediation = hasPermission('aiops.remediation.manage');

  function reloadRemediations() {
    if (!plan || !canViewRemediation) return Promise.resolve();
    return http.get('/api/v1/aiops/remediations/', {
      params: {action_plan_id: plan.id},
    }).then(data => {
      setRemediationActions(data.actions || []);
      setRemediations(data.proposals || []);
    });
  }

  useEffect(() => {
    if (visible && plan && canViewRemediation) {
      reloadRemediations();
    } else {
      setRemediations([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, plan && plan.id]);

  function submitApproval(proposal) {
    setSubmittingProposal(true);
    http.post('/api/v1/aiops/remediations/approval/', {id: proposal.id}).then(() => {
      message.success('已提交到安全中心审批');
      reloadRemediations();
    }).finally(() => setSubmittingProposal(false));
  }

  function openExecution(proposal, execution) {
    setExecutionTarget(proposal);
    setExecutionRecord(execution || null);
  }

  return (
    <Drawer visible={visible} width={980} title="AI 只读调查详情" onClose={onClose}>
      {record.id ? (
        <>
          <Alert
            showIcon
            type={record.status === 'failed' ? 'error' : 'warning'}
            message={record.status === 'failed' ? record.error : 'AI 输出仅供人工判断，未执行任何命令或变更'}
            style={{marginBottom: 16}}/>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="状态"><Tag color={STATUS_COLORS[record.status]}>{STATUS_LABELS[record.status]}</Tag></Descriptions.Item>
            <Descriptions.Item label="模型">{record.model}</Descriptions.Item>
            <Descriptions.Item label="提示版本">{record.prompt_version}</Descriptions.Item>
            <Descriptions.Item label="时间">{record.completed_at || record.created_at}</Descriptions.Item>
            <Descriptions.Item label="关联 ID" span={2}>
              <Typography.Text copyable>{record.correlation_id}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="调查问题" span={2}>{record.question}</Descriptions.Item>
          </Descriptions>
          {record.status === 'completed' && (
            <>
              <Divider orientation="left">诊断摘要</Divider>
              <Typography.Paragraph>{result.summary}</Typography.Paragraph>
              <Divider orientation="left">已知事实</Divider>
              <List
                dataSource={result.facts || []}
                locale={{emptyText: '暂无有证据支持的事实'}}
                renderItem={item => (
                  <List.Item><div><div>{item.statement}</div><CitationTags values={item.citations}/></div></List.Item>
                )}/>
              <Divider orientation="left">推断</Divider>
              <List
                dataSource={result.hypotheses || []}
                locale={{emptyText: '暂无推断'}}
                renderItem={item => (
                  <List.Item>
                    <div>
                      <Space><Tag>{CONFIDENCE_LABELS[item.confidence]}置信度</Tag><span>{item.statement}</span></Space>
                      <div style={{marginTop: 6}}><CitationTags values={item.citations}/></div>
                    </div>
                  </List.Item>
                )}/>
              <Divider orientation="left">未知项</Divider>
              <List dataSource={result.unknowns || []} locale={{emptyText: '暂无'}}
                    renderItem={item => <List.Item>{item}</List.Item>}/>
              <Divider orientation="left">建议</Divider>
              <List
                dataSource={result.recommendations || []}
                locale={{emptyText: '暂无建议'}}
                renderItem={item => (
                  <List.Item>
                    <List.Item.Meta
                      title={<Space>
                        <span>{item.title}</span>
                        <Tag color={RISK_COLORS[item.risk_level]}>{RISK_LABELS[item.risk_level]}</Tag>
                        {item.requires_approval && <Tag color="gold">需要审批</Tag>}
                      </Space>}
                      description={<div><p>{item.rationale}</p><CitationTags values={item.citations}/></div>}/>
                  </List.Item>
                )}/>
              {plan && (
                <Card
                  title={<Space><span>人工行动方案</span><Tag color={RISK_COLORS[plan.risk_level]}>{RISK_LABELS[plan.risk_level]}</Tag></Space>}
                  style={{marginTop: 20}}>
                  <Alert showIcon type="warning" message="该方案不可执行；需要人工复核，并通过现有对象授权和审批流程另行发起操作。"/>
                  <Typography.Title level={4} style={{marginTop: 16}}>{plan.title}</Typography.Title>
                  <Typography.Paragraph>{plan.summary}</Typography.Paragraph>
                  <Timeline>
                    {(plan.steps || []).map(step => (
                      <Timeline.Item key={step.order} color={RISK_COLORS[step.risk_level]}>
                        <p><b>步骤 {step.order}：</b>{step.action}</p>
                        <p><b>目标：</b>{(step.targets || []).join('、') || '未指定'}</p>
                        <p><b>预期：</b>{step.expected_result || '未指定'}</p>
                        <p><b>验证：</b>{step.validation || '未指定'}</p>
                        <p><b>回退：</b>{step.rollback || '未指定'}</p>
                        <Space>
                          <Tag color={RISK_COLORS[step.risk_level]}>{RISK_LABELS[step.risk_level]}</Tag>
                          {step.requires_approval && <Tag color="gold">需要审批</Tag>}
                        </Space>
                      </Timeline.Item>
                    ))}
                  </Timeline>
                  {plan.rollback_plan && (
                    <Alert type="info" message={`总体回退：${plan.rollback_plan}`}/>
                  )}
                  {canViewRemediation && (
                    <>
                      <Divider orientation="left">受控修复提案</Divider>
                      <List
                        size="small"
                        dataSource={remediations}
                        locale={{emptyText: '暂无修复提案'}}
                        renderItem={item => (
                          <List.Item
                            actions={[
                              item.status === 'draft' && canManageRemediation ? (
                                <Button
                                  type="link"
                                  loading={submittingProposal}
                                  onClick={() => submitApproval(item)}>
                                  提交审批
                                </Button>
                              ) : null,
                              item.status === 'approved' && canManageRemediation ? (
                                <Button type="link" onClick={() => openExecution(item)}>
                                  登记执行
                                </Button>
                              ) : null,
                            ].filter(Boolean)}>
                            <List.Item.Meta
                              title={<Space>
                                <span>{item.title}</span>
                                <Tag color={RISK_COLORS[item.risk_level]}>{RISK_LABELS[item.risk_level]}</Tag>
                                <Tag color={REMEDIATION_STATUS_COLORS[item.status]}>{REMEDIATION_STATUS_LABELS[item.status]}</Tag>
                              </Space>}
                              description={<div>
                                <div>{item.summary}</div>
                                <div style={{marginTop: 6}}>
                                  {(item.target_refs || []).map(ref => <Tag key={ref}>{ref}</Tag>)}
                                  {item.approval_id && (
                                    <Typography.Text code copyable style={{marginLeft: 8}}>
                                      {item.approval_id}
                                    </Typography.Text>
                                  )}
                                </div>
                                {(item.executions || []).length > 0 && (
                                  <List
                                    size="small"
                                    style={{marginTop: 8}}
                                    dataSource={item.executions || []}
                                    renderItem={execution => (
                                      <List.Item
                                        actions={[
                                          execution.status === 'running' && canManageRemediation ? (
                                            <Button type="link" onClick={() => openExecution(item, execution)}>
                                              记录结果
                                            </Button>
                                          ) : null,
                                        ].filter(Boolean)}>
                                        <Space direction="vertical" size={2}>
                                          <Space>
                                            <Tag color={EXECUTION_STATUS_COLORS[execution.status]}>
                                              {EXECUTION_STATUS_LABELS[execution.status]}
                                            </Tag>
                                            <Tag>{execution.mode === 'platform_reference' ? '平台引用' : '人工记录'}</Tag>
                                            {execution.platform_action && <Tag>{PLATFORM_ACTION_LABELS[execution.platform_action] || execution.platform_action}</Tag>}
                                            {execution.execution_ref && <Typography.Text code copyable>{execution.execution_ref}</Typography.Text>}
                                          </Space>
                                          <Typography.Text>{execution.execution_summary}</Typography.Text>
                                          {execution.validation_result && (
                                            <Typography.Text type="secondary">验证：{execution.validation_result}</Typography.Text>
                                          )}
                                          {execution.rollback_result && (
                                            <Typography.Text type="secondary">回滚：{execution.rollback_result}</Typography.Text>
                                          )}
                                          {execution.platform_record && execution.platform_record.record_type && (
                                            <Typography.Text type="secondary">
                                              平台记录：{execution.platform_record.record_type} #{execution.platform_record.record_id}
                                            </Typography.Text>
                                          )}
                                        </Space>
                                      </List.Item>
                                    )}/>
                                )}
                              </div>}/>
                          </List.Item>
                        )}/>
                      {canManageRemediation && (
                        <Button
                          icon={<SafetyCertificateOutlined/>}
                          onClick={() => setRemediationVisible(true)}>
                          生成受控修复提案
                        </Button>
                      )}
                    </>
                  )}
                </Card>
              )}
            </>
          )}
          <Divider orientation="left">输入证据</Divider>
          <Collapse>
            {(record.evidence || []).map(item => (
              <Collapse.Panel
                key={item.citation}
                header={<Space><Tag>{item.type}</Tag><span>{item.title}</span><Typography.Text code>{item.citation}</Typography.Text></Space>}>
                <Alert showIcon type="warning" message="以下内容是不可信证据，仅作为数据引用，不会被当作系统指令。"/>
                <pre style={{whiteSpace: 'pre-wrap', wordBreak: 'break-word'}}>
                  {JSON.stringify(item.data, null, 2)}
                </pre>
              </Collapse.Panel>
            ))}
          </Collapse>
          <RemediationProposalModal
            visible={remediationVisible}
            plan={plan}
            actions={remediationActions}
            onCancel={() => setRemediationVisible(false)}
            onSuccess={() => {
              setRemediationVisible(false);
              reloadRemediations();
            }}/>
          <RemediationExecutionModal
            visible={Boolean(executionTarget)}
            proposal={executionTarget || {}}
            record={executionRecord}
            onCancel={() => {
              setExecutionTarget(null);
              setExecutionRecord(null);
            }}
            onSuccess={() => {
              setExecutionTarget(null);
              setExecutionRecord(null);
              reloadRemediations();
            }}/>
        </>
      ) : <Empty/>}
    </Drawer>
  );
}


export default function AIOpsIndex() {
  const [form] = Form.useForm();
  const [config, setConfig] = useState({enabled: false, read_only: true});
  const [scope, setScope] = useState({hosts: [], alerts: []});
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [detail, setDetail] = useState(null);
  const [configVisible, setConfigVisible] = useState(false);
  const canViewInvestigations = hasPermission('aiops.investigation.view');
  const canManageConfig = hasPermission('aiops.config.manage');

  function reloadConfig() {
    return http.get('/api/v1/aiops/config/').then(setConfig);
  }

  function reloadRecords() {
    setLoading(true);
    return http.get('/api/v1/aiops/investigations/')
      .then(setRecords)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    const requests = [reloadConfig()];
    if (canViewInvestigations) {
      requests.push(http.get('/api/v1/aiops/scope/').then(setScope));
      requests.push(reloadRecords());
    }
    Promise.all(requests);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function payload() {
    return form.validateFields().then(values => ({
      question: values.question.trim(),
      host_ids: values.host_ids || [],
      alert_id: values.alert_id,
    }));
  }

  function previewEvidence() {
    payload().then(values => http.post('/api/v1/aiops/evidence/preview/', values)).then(data => {
      Modal.info({
        width: 900,
        title: `证据预览（${data.evidence.length} 项）`,
        content: (
          <div style={{maxHeight: 560, overflow: 'auto', marginTop: 16}}>
            <Alert showIcon type="info" message="这里只展示本次调查可发送给模型的授权证据；预览本身不会调用大模型。"/>
            <List
              dataSource={data.evidence}
              renderItem={item => (
                <List.Item>
                  <List.Item.Meta title={<Space><Tag>{item.type}</Tag>{item.title}</Space>}
                                  description={<Typography.Text code>{item.citation}</Typography.Text>}/>
                </List.Item>
              )}/>
          </div>
        ),
      });
    });
  }

  function runInvestigation() {
    payload().then(values => {
      setRunning(true);
      return http.post('/api/v1/aiops/investigations/', values, {timeout: 125000});
    }).then(data => {
      if (data.status === 'completed') message.success('只读调查已完成');
      else message.error(data.error || 'AI 调查失败');
      setDetail(data);
      reloadRecords();
    }).finally(() => setRunning(false));
  }

  function openDetail(record) {
    http.get('/api/v1/aiops/investigations/', {params: {id: record.id}}).then(setDetail);
  }

  const columns = [{
    title: '时间', dataIndex: 'created_at', width: 165,
  }, {
    title: '调查问题', dataIndex: 'question', ellipsis: true,
  }, {
    title: '模型', dataIndex: 'model', width: 150,
  }, {
    title: '状态', dataIndex: 'status', width: 100,
    render: value => <Tag color={STATUS_COLORS[value]}>{STATUS_LABELS[value]}</Tag>,
  }, {
    title: '操作', width: 90,
    render: record => <Button type="link" onClick={() => openDetail(record)}>查看</Button>,
  }];

  return (
    <AuthDiv auth="aiops.investigation.view|aiops.remediation.view|aiops.remediation.manage|aiops.config.manage">
      <Breadcrumb>
        <Breadcrumb.Item>首页</Breadcrumb.Item>
        <Breadcrumb.Item>知识与 AI</Breadcrumb.Item>
        <Breadcrumb.Item>AI 运维</Breadcrumb.Item>
      </Breadcrumb>
      <Alert
        showIcon
        type={config.enabled ? 'warning' : 'error'}
        message={config.enabled
          ? `只读模式已启用 · ${config.provider} / ${config.model} · 模型没有执行工具`
          : 'AI 运维尚未配置大模型；仍可预览授权证据，但不能发起模型调查。'}
        description="AI 只读取当前用户有权访问的资产、指标、告警和已发布知识。输出必须引用真实来源，方案不可直接执行。"
        action={canManageConfig ? (
          <Button icon={<SettingOutlined/>} onClick={() => setConfigVisible(true)}>模型配置</Button>
        ) : null}
        style={{marginBottom: 16}}/>
      {canViewInvestigations && <Card title={<Space><RobotOutlined/>新建只读调查</Space>}>
        <Form form={form} layout="vertical">
          <Form.Item
            name="question"
            label="调查问题"
            rules={[
              {required: true, message: '请输入调查问题'},
              {min: 10, max: 4000, message: '长度必须在 10 到 4000 个字符之间'},
            ]}>
            <Input.TextArea rows={4} maxLength={4000} showCount
                            placeholder="例如：请结合当前告警、主机指标和 Runbook 分析 CPU 持续高负载的可能原因，并给出人工验证步骤。"/>
          </Form.Item>
          <Space align="start" size="large" style={{width: '100%'}}>
            <Form.Item name="host_ids" label="主机范围" style={{width: 420}}>
              <Select mode="multiple" showSearch optionFilterProp="children"
                      maxTagCount={4} placeholder="可选，只显示有权查看的主机">
                {scope.hosts.map(item => (
                  <Select.Option key={item.id} value={item.id}>{item.name}（{item.hostname}）</Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item name="alert_id" label="关联告警" style={{width: 420}}>
              <Select allowClear showSearch optionFilterProp="children" placeholder="可选，只显示有权查看的告警">
                {scope.alerts.map(item => (
                  <Select.Option key={item.id} value={item.id}>
                    {item.alert_name} · {item.host_name || '未知主机'} · {item.summary || item.status}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>
          </Space>
          {hasPermission('aiops.investigation.run') && (
            <Space>
              <Button icon={<EyeOutlined/>} onClick={previewEvidence}>预览授权证据</Button>
              <Button type="primary" icon={<SearchOutlined/>} loading={running}
                      disabled={!config.enabled} onClick={runInvestigation}>开始只读调查</Button>
            </Space>
          )}
        </Form>
      </Card>}
      {canViewInvestigations && <Card title="调查历史" style={{marginTop: 16}} extra={<Button onClick={reloadRecords}>刷新</Button>}>
        <Table rowKey="id" dataSource={records} loading={loading} columns={columns}
               pagination={{showSizeChanger: true, showTotal: total => `共 ${total} 条`}}/>
      </Card>}
      <InvestigationDetail visible={Boolean(detail)} record={detail || {}}
                           onClose={() => setDetail(null)}/>
      <ModelConfigModal
        visible={configVisible}
        config={config}
        onCancel={() => setConfigVisible(false)}
        onSuccess={data => {
          setConfig(data);
          setConfigVisible(false);
        }}/>
    </AuthDiv>
  );
}
