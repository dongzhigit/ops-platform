/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import { Tooltip } from 'antd';
import React from 'react';

const Tips1 = '内置全局变量'

const Tips2 = (
  <Tooltip title="配置中心应用的配置将会以 _SPUG_标识符_Key 方式组合成环境变量，可通过执行 env | grep SPUG 来查看所有的内置的和配置中心的可使用变量。">
    <span style={{color: '#2563fc'}}>配置中心的配置变量</span>
  </Tooltip>
)

export default (
  <span>可使用 {Tips1} 和 {Tips2}</span>
)
