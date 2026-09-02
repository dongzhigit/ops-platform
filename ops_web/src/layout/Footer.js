/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { Layout } from 'antd';
import { CopyrightOutlined } from '@ant-design/icons';
import styles from './layout.module.less';


export default function () {
  return (
    <Layout.Footer style={{padding: 0}}>
      <div className={styles.footer}>
        <div style={{color: 'rgba(0, 0, 0, .45)'}}>
          Copyright <CopyrightOutlined/> {new Date().getFullYear()} By ops-platform
        </div>
      </div>
    </Layout.Footer>
  )
}
