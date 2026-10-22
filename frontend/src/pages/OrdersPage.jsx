import React from 'react';
import BooksDocumentsPage from './BooksDocumentsPage';

export function OrdersPage({ setError }) {
  return <BooksDocumentsPage type="sales-order" setError={setError} />;
}
