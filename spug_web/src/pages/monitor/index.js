/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { observer } from 'mobx-react';
import { Tabs } from 'antd';
import { AuthDiv, Breadcrumb } from 'components';
import { hasPermission } from 'libs';
import ComTable from './Table';
import ComForm from './Form';
import MonitorCard from './MonitorCard';
import store from './store';
import MetricsPanel from './MetricsPanel';

export default observer(function () {
  return (
    <AuthDiv auth="monitor.monitor.view|monitor.metrics.view">
      <Breadcrumb>
        <Breadcrumb.Item>首页</Breadcrumb.Item>
        <Breadcrumb.Item>监控中心</Breadcrumb.Item>
      </Breadcrumb>
      <Tabs defaultActiveKey={hasPermission('monitor.metrics.view') ? 'metrics' : 'probe'}>
        {hasPermission('monitor.metrics.view') && (
          <Tabs.TabPane key="metrics" tab="主机指标">
            <MetricsPanel/>
          </Tabs.TabPane>
        )}
        {hasPermission('monitor.monitor.view') && (
          <Tabs.TabPane key="probe" tab="站点与任务探测">
            <MonitorCard/>
            <ComTable/>
            {store.formVisible && <ComForm/>}
          </Tabs.TabPane>
        )}
      </Tabs>
    </AuthDiv>
  )
})
