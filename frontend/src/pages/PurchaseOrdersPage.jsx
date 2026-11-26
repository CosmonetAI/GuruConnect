import React from 'react';
import BooksDocumentsPage from './BooksDocumentsPage';

export function PurchaseOrdersPage({ setError }) {
  return <BooksDocumentsPage type="purchase-order" setError={setError} />;
}
