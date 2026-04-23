import React, { createContext, useContext, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Check, X, Info } from 'lucide-react';
import { cn } from '../lib/utils';

type ToastType = 'success' | 'error' | 'info';

interface ToastData {
  id: string;
  message: string;
  type: ToastType;
}

interface ToastContextType {
  showToast: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextType | null>(null);

export const useToast = () => {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used within ToastProvider");
  return context;
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastData[]>([]);

  const showToast = useCallback((message: string, type: ToastType = 'info') => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts(prev => [...prev, { id, message, type }]);
    
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 4000);
  }, []);

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className="fixed bottom-6 right-6 z-[9999] flex flex-col gap-2 pointer-events-none">
        <AnimatePresence>
          {toasts.map(t => (
            <motion.div
              layout
              key={t.id}
              initial={{ opacity: 0, y: 30, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95, transition: { duration: 0.2 } }}
              className={cn(
                "px-4 py-3 rounded-2xl border flex items-center gap-3 shadow-2xl backdrop-blur-xl font-medium text-sm min-w-[280px] pointer-events-auto",
                t.type === 'success' ? "bg-[#0A0A0B] border-emerald-500/30 text-emerald-400" :
                t.type === 'error' ? "bg-[#0A0A0B] border-rose-500/30 text-rose-400" :
                "bg-[#0A0A0B] border-zinc-700 text-white"
              )}
            >
              {t.type === 'success' && <Check size={18} />}
              {t.type === 'error' && <X size={18} />}
              {t.type === 'info' && <Info size={18} className="text-zinc-400" />}
              {t.message}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
};
