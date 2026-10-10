#pragma once

#include <atomic>
#include <array>
#include <optional>

// Single-Producer Single-Consumer lock-free queue
// Safe to use between audio thread and command thread

template<typename T, size_t Capacity>
class LockFreeQueue {
public:
    LockFreeQueue() : head_(0), tail_(0) {}
    
    // Called from producer thread (command thread)
    bool push(const T& item) {
        const size_t currentTail = tail_.load(std::memory_order_relaxed);
        const size_t nextTail = (currentTail + 1) % Capacity;
        
        if (nextTail == head_.load(std::memory_order_acquire)) {
            return false; // Queue is full
        }
        
        buffer_[currentTail] = item;
        tail_.store(nextTail, std::memory_order_release);
        return true;
    }
    
    // Called from consumer thread (audio thread)
    std::optional<T> pop() {
        const size_t currentHead = head_.load(std::memory_order_relaxed);
        
        if (currentHead == tail_.load(std::memory_order_acquire)) {
            return std::nullopt; // Queue is empty
        }
        
        T item = buffer_[currentHead];
        head_.store((currentHead + 1) % Capacity, std::memory_order_release);
        return item;
    }
    
    bool empty() const {
        return head_.load(std::memory_order_acquire) == tail_.load(std::memory_order_acquire);
    }
    
    size_t size() const {
        const size_t head = head_.load(std::memory_order_acquire);
        const size_t tail = tail_.load(std::memory_order_acquire);
        return (tail >= head) ? (tail - head) : (Capacity - head + tail);
    }

private:
    std::array<T, Capacity> buffer_;
    std::atomic<size_t> head_;
    std::atomic<size_t> tail_;
};

