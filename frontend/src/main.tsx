import React from 'react';
import ReactDOM from 'react-dom/client';
import { App as AntdApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import 'antd/dist/reset.css';
import './conversation.css';
import App from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} button={{ autoInsertSpace: false }} theme={{
      token: {
        colorPrimary: '#1677ff',
        colorText: '#1f1f1f',
        colorTextSecondary: '#595959',
        colorBgLayout: '#f5f5f5',
        colorBorder: '#d9d9d9',
        borderRadius: 8,
        fontSize: 13,
        fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        controlHeight: 34,
      },
      components: {
        Button: { primaryShadow: 'none' },
        Tabs: { horizontalMargin: '0 0 20px 0' },
      },
    }}>
      <AntdApp>
        <App />
      </AntdApp>
    </ConfigProvider>
  </React.StrictMode>,
);
