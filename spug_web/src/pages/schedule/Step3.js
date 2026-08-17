import React, { useState } from 'react';
import { observer } from 'mobx-react';
import { Form, Tabs, DatePicker, InputNumber, Input, Button, message } from 'antd';
import { LoadingOutlined } from '@ant-design/icons';
import { http } from 'libs';
import { ApprovalGate } from 'components';
import store from './store';
import moment from 'moment';
import lds from 'lodash';

let lastFetchId = 0;

export default observer(function () {
  const [loading, setLoading] = useState(false);
  const [trigger, setTrigger] = useState(store.record.trigger);
  const [args, setArgs] = useState({[store.record.trigger]: store.record.trigger_args});
  const [nextRunTime, setNextRunTime] = useState(null);
  const [approvalRequest, setApprovalRequest] = useState();

  function handleSubmit() {
    if (trigger === 'date' && args['date'] <= moment()) {
      return message.error('任务执行时间不能早于当前时间')
    }
    const targets = store.targets.filter(x => x);
    const triggerArgs = _parse_args();
    const payload = {
      host_ids: targets.filter(x => String(x) !== 'local').sort((a, b) => a - b),
      targets,
      name: store.record.name,
      interpreter: store.record.interpreter,
      command: store.record.command,
      trigger,
      trigger_args: triggerArgs,
    }
    setApprovalRequest({
      operation: {
        action: 'schedule.write',
        resource_type: 'host',
        resource_ids: targets,
        payload,
      },
      formData: {
        ...lds.pick(store.record, ['id', 'name', 'type', 'interpreter', 'command', 'desc', 'rst_notify']),
        targets,
        trigger,
        trigger_args: triggerArgs,
      },
      summary: `${store.record.id ? '修改' : '创建'}计划任务：${store.record.name}`,
    })
  }

  function saveSchedule(request, approvalId) {
    setLoading(true)
    return http.post('/api/schedule/', {...request.formData, approval_id: approvalId})
      .then(() => {
        message.success('操作成功');
        store.fetchRecords()
      })
      .finally(() => setLoading(false))
  }

  function handleArgs(key, val) {
    setArgs(Object.assign({}, args, {[key]: val}))
  }

  function handleCronArgs(key, val) {
    let tmp = args['cron'] || {};
    tmp = Object.assign(tmp, {[key]: val});
    setArgs(Object.assign({}, args, {cron: tmp}));
    _fetchNextRunTime()
  }

  function _parse_args() {
    switch (trigger) {
      case 'date':
        return moment(args['date']).format('YYYY-MM-DD HH:mm:ss');
      case 'cron':
        const {rule, start, stop} = args['cron'];
        return JSON.stringify({
          rule,
          start: start ? moment(start).format('YYYY-MM-DD HH:mm:ss') : null,
          stop: stop ? moment(stop).format('YYYY-MM-DD HH:mm:ss') : null
        });
      default:
        return String(args[trigger]);
    }
  }

  function _fetchNextRunTime() {
    if (trigger === 'cron') {
      const rule = lds.get(args, 'cron.rule');
      if (rule && rule.trim().split(/ +/).length === 5) {
        setNextRunTime(<LoadingOutlined/>);
        lastFetchId += 1;
        const fetchId = lastFetchId;
        const args = _parse_args();
        http.post('/api/schedule/run_time/', JSON.parse(args))
          .then(res => {
            if (fetchId !== lastFetchId) return;
            if (res.success) {
              setNextRunTime(<span style={{fontSize: 12, color: '#52c41a'}}>{res.msg}</span>)
            } else {
              setNextRunTime(<span style={{fontSize: 12, color: '#ff4d4f'}}>{res.msg}</span>)
            }
          })
      } else {
        setNextRunTime(null)
      }
    }
  }

  return (
    <React.Fragment>
      <Form layout="vertical" wrapperCol={{span: 14, offset: 6}}>
      <Form.Item>
        <Tabs activeKey={trigger} onChange={setTrigger} tabPosition="left" style={{minHeight: 200}}>
          <Tabs.TabPane tab="普通间隔" key="interval">
            <Form.Item required label="间隔时间(秒)" extra="每隔指定n秒执行一次。">
              <InputNumber
                style={{width: 200}}
                placeholder="请输入"
                value={args['interval']}
                onChange={v => handleArgs('interval', v)}/>
            </Form.Item>
          </Tabs.TabPane>
          <Tabs.TabPane tab="一次性" key="date">
            <Form.Item required label="执行时间" extra="仅在指定时间运行一次。">
              <DatePicker
                showTime
                disabledDate={v => v && v.format('YYYY-MM-DD') < moment().format('YYYY-MM-DD')}
                style={{width: 200}}
                placeholder="请选择执行时间"
                onOk={() => false}
                value={args['date'] ? moment(args['date']) : undefined}
                onChange={v => handleArgs('date', v)}/>
            </Form.Item>
          </Tabs.TabPane>
          <Tabs.TabPane tab="UNIX Cron" key="cron">
            <Form.Item required label="执行规则" extra="兼容Cron风格，可参考官方例子">
              <Input
                suffix={nextRunTime || <span/>}
                value={lds.get(args, 'cron.rule')}
                placeholder="例如每天凌晨1点执行：0 1 * * *"
                onChange={e => handleCronArgs('rule', e.target.value)}/>
            </Form.Item>
            <Form.Item label="生效时间" extra="定义的执行规则在到达该时间后生效">
              <DatePicker
                showTime
                style={{width: '100%'}}
                placeholder="可选输入"
                value={lds.get(args, 'cron.start') ? moment(args['cron']['start']) : undefined}
                onChange={v => handleCronArgs('start', v)}/>
            </Form.Item>
            <Form.Item label="结束时间" extra="执行规则在到达该时间后不再执行">
              <DatePicker
                showTime
                style={{width: '100%'}}
                placeholder="可选输入"
                value={lds.get(args, 'cron.stop') ? moment(args['cron']['stop']) : undefined}
                onChange={v => handleCronArgs('stop', v)}/>
            </Form.Item>
          </Tabs.TabPane>
        </Tabs>
      </Form.Item>
      <Form.Item wrapperCol={{span: 14, offset: 6}}>
        <Button type="primary" loading={loading} disabled={!args[trigger]} onClick={handleSubmit}>提交</Button>
        <Button style={{marginLeft: 20}} onClick={() => store.page -= 1}>上一步</Button>
      </Form.Item>
      </Form>
      {approvalRequest && (
        <ApprovalGate
          operation={approvalRequest.operation}
          defaultSummary={approvalRequest.summary}
          onCancel={() => setApprovalRequest()}
          onComplete={() => store.formVisible = false}
          onExecute={approvalId => saveSchedule(approvalRequest, approvalId)}/>
      )}
    </React.Fragment>
  )
})
