// @flow
// page tabs bar

import * as React from 'react'
import classnames from 'classnames'
import {Link} from 'react-router-dom'

import styles from './structure.css'

type TabProps = {
  title: string,
  href: string,
  isActive: bool,
  isDisabled: bool
}

type Props = {
  pages: Array<TabProps>
}

export default function PageTabs (props: Props) {
  return (
    <nav className={styles.page_tabs}>
      {props.pages.map((page) => (
        <Tab key={page.title} {...page} />
      ))}
    </nav>
  )
}

function Tab (props: TabProps) {
  const {isDisabled} = props
  const tabLinkClass = props.isActive
    ? classnames(styles.tab_link, styles.active_tab_link)
    : styles.tab_link

  // TODO(mc, 2017-12-14): make a component for proper disabling of links
  const MaybeLink = !isDisabled
    ? Link
    : 'span'

  return (
    <MaybeLink to={props.href} className={tabLinkClass}>
      <h3 className={styles.tab_title}>
        {props.title}
      </h3>
    </MaybeLink>
  )
}
