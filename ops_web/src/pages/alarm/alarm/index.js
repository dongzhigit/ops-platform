/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { observer } from 'mobx-react';
import { SyncOutlined } from '@ant-design/icons';
import { Input, Button, Tabs } from 'antd';
import { SearchForm, AuthDiv, Breadcrumb } from 'components';
import { hasPermission } from 'libs';
import ComTable from './Table';
import AlertEvents from './AlertEvents';
import store from './store';

export default observer(function () {
  return (
    <AuthDiv auth="alarm.alarm.view|alarm.event.view">
      <Breadcrumb>
        <Breadcrumb.Item>首页</Breadcrumb.Item>
        <Breadcrumb.Item>报警中心</Breadcrumb.Item>
        <Breadcrumb.Item>报警历史</Breadcrumb.Item>
      </Breadcrumb>
      <Tabs defaultActiveKey={hasPermission('alarm.event.view') ? 'events' : 'legacy'}>
        {hasPermission('alarm.event.view') && (
          <Tabs.TabPane key="events" tab="指标告警工作台">
            <AlertEvents/>
          </Tabs.TabPane>
        )}
        {hasPermission('alarm.alarm.view') && (
          <Tabs.TabPane key="legacy" tab="探测告警历史">
            <SearchForm>
              <SearchForm.Item span={8} title="任务名称">
                <Input allowClear value={store.f_name} onChange={e => store.f_name = e.target.value} placeholder="请输入"/>
              </SearchForm.Item>
              <SearchForm.Item span={8}>
                <Button type="primary" icon={<SyncOutlined/>} onClick={store.fetchRecords}>刷新</Button>
              </SearchForm.Item>
            </SearchForm>
            <ComTable/>
          </Tabs.TabPane>
        )}
      </Tabs>
    </AuthDiv>
  )
})
